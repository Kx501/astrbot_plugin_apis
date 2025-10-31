from bs4 import BeautifulSoup
import asyncio
from collections import defaultdict
from typing import Optional, Union
import aiohttp
from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from .api_manager import APIManager
from .utils import dict_to_string, extract_url, get_nested_value, parse_api_keys

class RequestManager:
    def __init__(self, config: AstrBotConfig, api_manager: APIManager) -> None:
        self.session = aiohttp.ClientSession()
        # api密钥字典（从列表格式解析）
        api_keys_list = config.get("api_keys", [])
        self.api_key_dict = parse_api_keys(api_keys_list)
        self.api_sites = list(self.api_key_dict.keys())
        self.api = api_manager

    async def request(self,
        urls: list[str], params: Optional[dict] = None, test_mode:bool=False
    ) -> Union[bytes, str, dict, None]:
        last_exc = None
        for u in urls:
            try:
                async with self.session.get(u, params=params, timeout=30) as resp:
                    resp.raise_for_status()
                    if test_mode:
                        return
                    ct = resp.headers.get("Content-Type", "").lower()
                    if "application/json" in ct:
                        return await resp.json()
                    if "text/" in ct:
                        return (await resp.text()).strip()
                    return await resp.read()
            except Exception as e:
                last_exc = e
                logger.error(f"请求失败 {u}:{e}")
        if last_exc:
            raise last_exc

    async def get_data(
        self,
        urls: list[str],
        params: Optional[dict] = None,
        api_type: str = "",
        target: str = "",
    ) -> tuple[str | None, bytes | None]:
        """对外接口，获取数据"""

        data = await self.request(urls, params)

        # data为URL时，下载数据
        if isinstance(data, str) and api_type != "text":
            if url := extract_url(data):
                downloaded = await self.request(urls)
                if isinstance(downloaded, bytes):
                    data = downloaded
                else:
                    raise RuntimeError(f"下载数据失败: {url}")  # 抛异常给外部

        # data为字典时，解析字典
        if isinstance(data, dict) and target:
            nested_value = get_nested_value(data, target)
            if isinstance(nested_value, dict):
                data = dict_to_string(nested_value)
            else:
                data = nested_value

        # data为HTML字符串时，解析HTML
        if isinstance(data, str) and data.strip().startswith("<!DOCTYPE html>"):
            soup = BeautifulSoup(data, "html.parser")
            # 提取HTML中的文本内容
            data = soup.get_text(strip=True)

        text = data if isinstance(data, str) else None
        byte = data if isinstance(data, bytes) else None

        return text, byte

    async def batch_test_apis(self) -> tuple[list[str], list[str]]:
        """
        批量测试所有API功能的可用性。
        将每个请求地址按API站点分组；每轮从每个站点取一个地址并发测试。
        返回 (可用的API功能列表, 失效的API功能列表)
        """
        # 1) 展平每个API功能的所有请求地址 -> 按站点分组
        site_to_entries = defaultdict(list)  # 站点域名 -> 测试项列表
        for api_name, api_data in self.api.apis.items():
            url = api_data["url"]
            urls = [url] if isinstance(url, str) else url
            for u in urls:
                site = self.api.extract_base_url(u)  # 提取站点域名
                site_to_entries[site].append(
                    {
                        "api_name": api_name,
                        "url": u,
                        "params": api_data.get("params", {}),
                    }
                )

        # 2) 记录每个API功能是否已成功（任一请求地址成功即为成功）
        api_succeeded = dict.fromkeys(self.api.apis.keys(), False)

        # 3) 按轮次从每个站点各取一个请求地址并发测试，直到所有站点的测试项列表空
        while any(site_to_entries.values()):
            batch = []
            for site, entries in list(site_to_entries.items()):
                while entries:
                    batch.append(entries.pop(0))
                    break

            if not batch:
                break  # 没有需要测试的项目了

            # 并发测试这一轮的所有请求地址
            tasks = [self.request([e["url"]], e["params"], True) for e in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理结果：任何非 Exception 的返回都视为成功
            for entry, res in zip(batch, results):
                if isinstance(res, Exception):
                    pass
                else:
                    api_succeeded[entry["api_name"]] = True

        # 4) 汇总：返回可用的和失效的API功能列表
        abled = [k for k, v in api_succeeded.items() if v]
        disabled = [k for k, v in api_succeeded.items() if not v]
        return abled, disabled

    async def terminate(self):
        """关闭会话，断开连接"""
        await self.session.close()



