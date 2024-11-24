import json
from urllib.parse import quote, urlencode
import requests
import time
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass
import sys
from datetime import datetime, timedelta
import traceback
import threading

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'booking_{datetime.now().strftime("%Y%m%d")}.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class BookingConfig:
    """预订配置类
    """
    dateadd: int
    TimePeriod: int
    VenueNo: str
    FieldTypeNo: str
    cookies: Dict[str, str]

    @staticmethod
    def validate_time_period(time_period: int) -> bool:
        return time_period in [0, 1, 2]

    @classmethod
    def create_default(cls, cookies: Dict[str, str], time_period: int) -> 'BookingConfig':
        return cls(
            dateadd=7,  # 表示从今天开始往后推 dateadd 天的日子 例子：今天是11.23，获取11.29的票dateadd=5
            TimePeriod=time_period,  # 0表示上午 1表示下午 2表示晚上
            VenueNo='005',  # 005表示北洋园体育馆
            FieldTypeNo='017',  # 017表示羽毛球场
            cookies=cookies
        )


class VenueBookingSystem:
    def __init__(self, config: BookingConfig):
        self.config = config
        self.headers = {
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Connection': 'keep-alive',
            'Referer': f'http://vfmc.tju.edu.cn/Views/Field/FieldOrder.html?VenueNo={config.VenueNo}&FieldTypeNo={config.FieldTypeNo}&FieldType=Field',
            'User-Agent': 'Mozilla/5.0 (Linux; Android 12; Lenovo L79031 Build/SKQ1.220119.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/126.0.6478.71 Mobile Safari/537.36 XWEB/1260037 MMWEBSDK/20240404 MMWEBID/4282 MicroMessenger/8.0.49.2600(0x2800315A) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64',
            'X-Requested-With': 'XMLHttpRequest'
        }

    def get_available_fields(self) -> List[Dict]:
        """获取可用场地列表，增加了重试机制和错误处理
        """
        func_name = "get_available_fields"
        max_retries = 3
        retry_delay = 1  # 初始重试延迟（秒）

        for attempt in range(max_retries):
            try:
                url = f'http://vfmc.tju.edu.cn/Field/GetVenueStateNew?dateadd={self.config.dateadd}&TimePeriod={self.config.TimePeriod}&VenueNo={self.config.VenueNo}&FieldTypeNo={self.config.FieldTypeNo}&_={int(time.time() * 1000)}'
                response = requests.get(
                    url,
                    headers=self.headers,
                    cookies=self.config.cookies,
                    timeout=10
                )
                response.raise_for_status()

                response_json = response.json()

                if response_json.get("errorcode") == 0:
                    resultdata = json.loads(response_json.get("resultdata", "[]"))
                    available_fields = [item for item in resultdata if item["FieldState"] == "0"]

                    logger.info(f"[{func_name}] 成功获取场馆状态，找到 {len(available_fields)} 个可预订场地")
                    return available_fields
                else:
                    error_msg = f"获取场馆状态失败：错误代码 {response_json.get('errorcode')}, 错误信息：{response_json.get('message')}"
                    logger.error(f"[{func_name}] {error_msg}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (2 ** attempt))
                        continue
                    return []

            except requests.exceptions.RequestException as e:
                logger.error(
                    f"[{func_name}] 网络请求错误 (尝试 {attempt + 1}/{max_retries}): {str(e)}\n{traceback.format_exc()}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))
                    continue
                return []

            except json.JSONDecodeError as e:
                logger.error(f"[{func_name}] JSON解析错误: {str(e)}\n{traceback.format_exc()}")
                return []

            except Exception as e:
                logger.error(f"[{func_name}] 未预期的错误: {str(e)}\n{traceback.format_exc()}")
                return []

    def select_field(self, available_fields: List[Dict], preferred_time: Optional[str] = None) -> Optional[Dict]:
        """选择场地，支持按偏好时间选择
        """
        func_name = "select_field"
        try:
            if not available_fields:
                logger.warning(f"[{func_name}] 没有可预订的场地")
                return None
            import random
            random.shuffle(available_fields)

            if preferred_time:
                # 尝试找到首选时间的场地
                for field in available_fields:
                    if field['BeginTime'].startswith(preferred_time):
                        logger.info(
                            f"[{func_name}] 找到符合偏好时间的场地: {field['FieldName']}, 时间段为 {field['BeginTime']} - {field['EndTime']}")
                        return field

            # 如果没有指定首选时间或未找到匹配场地，返回第一个可用场地
            selected_field = available_fields[0]
            logger.info(
                f"[{func_name}] 选择场地: {selected_field['FieldName']}, 时间段为 {selected_field['BeginTime']} - {selected_field['EndTime']}")
            return selected_field

        except Exception as e:
            logger.error(f"[{func_name}] 选择场地时发生错误: {str(e)}\n{traceback.format_exc()}")
            return None

    def book_field(self, selected_field: Dict) -> bool:
        """预订场地
        """
        func_name = "book_field"
        try:
            if not selected_field:
                logger.warning(f"[{func_name}] 未选择场地，无法进行预订")
                return False

            checkdata = [{
                "FieldNo": selected_field["FieldNo"],
                "FieldTypeNo": selected_field["FieldTypeNo"],
                "FieldName": selected_field["FieldName"],
                "BeginTime": selected_field["BeginTime"],
                "Endtime": selected_field["EndTime"],
                "Price": selected_field["FinalPrice"],
                "DateAdd": self.config.dateadd
            }]

            query_params = {
                "checkdata": json.dumps(checkdata, ensure_ascii=False),
                "VenueNo": self.config.VenueNo,
                "OrderType": "Field"
            }

            payload = "&".join([f"{quote(key)}={quote(value)}" for key, value in query_params.items()])

            headers = self.headers.copy()
            headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'

            response = requests.post(
                "http://vfmc.tju.edu.cn/Field/OrderField",
                headers=headers,
                cookies=self.config.cookies,
                data=payload,
                timeout=10
            )
            response.raise_for_status()

            response_json = response.json()

            if response_json.get("errorcode") == 0 and response_json.get("message") == "":
                logger.info(f"[{func_name}] 预订成功！请前往微信网页查看订单详情")
                return True
            else:
                logger.error(
                    f"[{func_name}] 预订失败：错误代码 {response_json.get('errorcode')}, 错误信息：{response_json.get('message')}")
                return False

        except requests.exceptions.RequestException as e:
            logger.error(f"[{func_name}] 预订请求发送失败: {str(e)}\n{traceback.format_exc()}")
            return False

        except Exception as e:
            logger.error(f"[{func_name}] 预订过程中发生错误: {str(e)}\n{traceback.format_exc()}")
            return False


def wait_until_target_time():
    """等待直到目标时间
    """
    func_name = "wait_until_target_time"
    target_hour = 21  # 21点
    target_minute = 0  # 0分
    target_second = 0  # 0秒

    while True:
        current_time = datetime.now()
        target_time = current_time.replace(hour=target_hour, minute=target_minute, second=target_second, microsecond=0)

        # 如果当前时间已经过了今天的目标时间，则目标时间设置为明天
        if current_time.hour > target_hour or (
                current_time.hour == target_hour and current_time.minute >= target_minute):
            # target_time += timedelta(days=1)
            break

        time_diff = (target_time - current_time).total_seconds()

        if time_diff <= 0:
            logger.info(f"[{func_name}] 到达目标时间 {target_time.strftime('%Y-%m-%d %H:%M:%S')}，开始执行预订")
            break

        # 如果离目标时间还有超过60秒，每60秒输出一次日志
        if time_diff > 60:
            logger.info(f"[{func_name}] 等待中... 距离开始时间还有 {time_diff / 3600:.2f} 小时")
            time.sleep(60)
        # 如果离目标时间不到60秒，每秒输出一次日志
        else:
            logger.info(f"[{func_name}] 等待中... 距离开始时间还有 {time_diff:.0f} 秒")
            time.sleep(1)


def book_field_thread(cookies: Dict[str, str], preferred_time: Optional[str], time_period: int, success_count: List[int]):
    func_name = "book_field_thread"
    try:
        # 创建配置对象
        config = BookingConfig.create_default(cookies, time_period)

        # 创建预订系统实例
        booking_system = VenueBookingSystem(config)

        max_attempts = 50  # 最大尝试次数
        attempt = 0

        while attempt < max_attempts:
            attempt += 1
            logger.info(f"[{func_name}] 第 {attempt} 次尝试预订")

            # 获取可用场地
            available_fields = booking_system.get_available_fields()

            if not available_fields:
                if attempt < max_attempts:
                    logger.warning(f"[{func_name}] 未找到可用场地，等待1秒后重试")
                    time.sleep(1)
                    continue
                else:
                    logger.error(f"[{func_name}] 达到最大尝试次数，仍未找到可用场地，程序退出")
                    break

            # 选择场地
            selected_field = booking_system.select_field(available_fields, preferred_time=preferred_time)

            if not selected_field:
                if attempt < max_attempts:
                    logger.warning(f"[{func_name}] 场地选择失败，等待1秒后重试")
                    time.sleep(1)
                    continue
                else:
                    logger.error(f"[{func_name}] 达到最大尝试次数，场地选择仍然失败，程序退出")
                    break

            # 预订场地
            success = booking_system.book_field(selected_field)

            if success:
                logger.info(f"[{func_name}] 预订成功！")
                success_count[0] += 1
                break
            elif attempt < max_attempts:
                logger.warning(f"[{func_name}] 预订失败，等待0.5秒后重试")
                time.sleep(1)
            else:
                logger.error(f"[{func_name}] 达到最大尝试次数，预订仍然失败，程序退出")

    except Exception as e:
        logger.error(f"[{func_name}] 线程执行过程中发生错误: {str(e)}\n{traceback.format_exc()}")


def main():
    func_name = "main"
    # 配置信息
    cookies_list = [
        {
            'WXOpenId': '',
            'LoginSource': '0',
            'JWTUserToken': '',
            'UserId': '',
            'LoginType': '1'
        },
        {
            'WXOpenId': '',
            'LoginSource': '0',
            'JWTUserToken': '',
            'UserId': '',
            'LoginType': '1'
        }
    ]

    try:
        # 等待直到目标时间
        # wait_until_target_time()

        success_count = [0]
        preferred_time_list = ["16:00", "17:00"]
        time_period_list = [1, 2]  # 不同的时间段，1表示下午，2表示晚上

        threads = []
        for cookies, preferred_time, time_period in zip(cookies_list, preferred_time_list, time_period_list):
            t = threading.Thread(target=book_field_thread, args=(cookies, preferred_time, time_period, success_count))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        if success_count[0] == len(cookies_list):
            logger.info(f"[{func_name}] 成功预订两个时段！")
        else:
            logger.warning(f"[{func_name}] 未能成功预订两个时段")

    except Exception as e:
        logger.error(f"[{func_name}] 程序执行过程中发生错误: {str(e)}\n{traceback.format_exc()}")


if __name__ == "__main__":
    main()