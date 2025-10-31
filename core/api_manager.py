
import ast
import copy
import json
import os
import random
from typing import Any
from urllib.parse import urlparse
from astrbot.api import logger


class APIManager:
    """API功能管理器 - 统一管理所有API功能"""

    ALLOWED_TYPES = ["text", "image", "video", "audio"]  # 支持的API数据类型常量

    def __init__(self, system_api_file, apis_file):
        """
        初始化API功能管理器
        :param system_api_file: 系统默认API功能文件路径（仅首次加载时使用）
        :param apis_file: 统一的API功能文件路径（所有API功能存储在此）
        """
        self.system_api_file = system_api_file
        self.apis_file = apis_file
        self.apis = {}
        self.default_api_type = "image"
        self.load_data()

    def load_data(self):
        """从统一的JSON文件加载API功能数据"""
        # 如果apis.json不存在，从system_api.json初始化
        if not os.path.exists(self.apis_file):
            self._initialize_from_system()
        else:
            # 加载现有API功能
            try:
                with open(self.apis_file, "r", encoding="utf-8") as file:
                    self.apis = json.load(file)
                logger.info(f"已加载{len(self.apis)}个API功能")
            except json.JSONDecodeError:
                logger.warning(f"{self.apis_file} 格式错误，尝试从系统默认文件重新初始化")
                self._initialize_from_system()

    def _initialize_from_system(self):
        """从系统默认API文件初始化"""
        if os.path.exists(self.system_api_file):
            try:
                with open(self.system_api_file, "r", encoding="utf-8") as file:
                    self.apis = json.load(file)
                # 保存到apis.json
                self._save_data()
                logger.info(f"已从系统默认文件初始化{len(self.apis)}个API功能到 {self.apis_file}")
            except json.JSONDecodeError:
                logger.error(f"{self.system_api_file} 格式错误，无法初始化")
                self.apis = {}
        else:
            logger.warning(f"系统默认API文件不存在: {self.system_api_file}")
            self.apis = {}

    def _save_data(self):
        """保存API功能数据到统一文件"""
        os.makedirs(os.path.dirname(self.apis_file), exist_ok=True)
        with open(self.apis_file, "w", encoding="utf-8") as file:
            json.dump(self.apis, file, ensure_ascii=False, indent=4)

    def add_api(self, api_info: dict):
        """添加或更新一个API功能"""
        name = api_info.get("keyword", [""])[0] if isinstance(api_info.get("keyword"), list) else api_info.get("keyword", "")
        if not name:
            logger.error("API功能中缺少keyword字段")
            return
        self.apis[name] = api_info
        self._save_data()
        logger.info(f"已添加/更新API功能: {name}")

    def remove_api(self, name: str):
        """移除一个API功能（通过触发词）"""
        if name in self.apis:
            del self.apis[name]
            self._save_data()
            logger.info(f"已删除API功能: {name}")
        else:
            logger.warning(f"API功能 '{name}' 不存在")

    @staticmethod
    def extract_base_url(full_url: str) -> str:
        """
        从完整URL中提取API站点域名（主域部分），例如：
        输入: "https://api.pearktrue.cn/api/stablediffusion/"
        输出: "https://api.pearktrue.cn"
        :param full_url: 完整的API请求地址
        :return: API站点域名
        """
        parsed = urlparse(full_url)
        return (
            f"{parsed.scheme}://{parsed.netloc}"
            if parsed.scheme and parsed.netloc
            else full_url
        )

    def get_apis_names(self):
        """获取所有API的名称（返回所有API的key）"""
        return list(self.apis.keys())

    def normalize_api_data(self, name: str) -> dict:
        """标准化 API 配置，返回深拷贝，避免被外部修改"""
        raw_api = self.apis.get(name, {})
        url = raw_api.get("url", "")
        urls = [url] if isinstance(url, str) else url

        api_type = raw_api.get("type", "")
        if api_type not in self.ALLOWED_TYPES:
            api_type = self.default_api_type

        normalized = {
            "name": name,
            "urls": urls,
            "type": api_type,
            "params": raw_api.get("params", {}) or {},
            "target": raw_api.get("target", ""),
            "fuzzy": raw_api.get("fuzzy", False),
            "priority": raw_api.get("priority", 0),  # 优先级支持，默认0
        }
        return copy.deepcopy(normalized)

    def match_api_by_name(self, msg: str) -> dict | None:
        """
        通过触发词匹配API功能，返回匹配的功能（按优先级选择最佳匹配）。
        如果有多个匹配，按优先级返回最高的。
        :param msg: 触发词
        :return: 匹配的API功能数据，如果未匹配则返回None
        """
        matches = []
        
        for key, raw_api in self.apis.items():
            keywords = raw_api.get("keyword", [])
            if isinstance(keywords, str):
                keywords = [keywords]

            matched = False
            # 精准匹配
            if msg in keywords:
                matched = True
            # 模糊匹配
            elif raw_api.get("fuzzy", False) and any(k in msg for k in keywords):
                matched = True

            if matched:
                priority = raw_api.get("priority", 0)
                matches.append((priority, key, raw_api))
        
        if not matches:
            return None
        
        # 按优先级排序（数字越大优先级越高），相同优先级时随机选择
        matches.sort(key=lambda x: x[0], reverse=True)
        
        # 获取最高优先级的匹配（可能有多个相同优先级）
        max_priority = matches[0][0]
        same_priority_matches = [m for m in matches if m[0] == max_priority]
        
        # 相同优先级时随机选择一个
        selected = random.choice(same_priority_matches)
        return self.normalize_api_data(selected[1])
    
    def find_api_matches(self, msg: str) -> list[tuple[int, str, dict]]:
        """
        查找所有匹配的API功能，返回 (priority, 触发词, api_data) 列表，按优先级排序。
        用于需要获取所有匹配的场景。
        :param msg: 触发词
        :return: (优先级, 触发词, API功能数据) 的列表
        """
        matches = []
        
        for key, raw_api in self.apis.items():
            keywords = raw_api.get("keyword", [])
            if isinstance(keywords, str):
                keywords = [keywords]

            matched = False
            # 精准匹配或模糊匹配
            if msg in keywords or (raw_api.get("fuzzy", False) and any(k in msg for k in keywords)):
                matched = True

            if matched:
                priority = raw_api.get("priority", 0)
                matches.append((priority, key, raw_api))
        
        # 按优先级排序
        matches.sort(key=lambda x: x[0], reverse=True)
        return matches

    def list_api(self):
        """
        根据API功能字典生成分类字符串，即API功能列表。
        按数据类型（text/image/video/audio）分类展示。
        """
        # 用 ALLOWED_TYPES 初始化分类字典
        api_types = {t: [] for t in self.ALLOWED_TYPES}

        # 遍历apis字典，按type分类
        for key, value in self.apis.items():
            api_type = value.get("type", "unknown")
            if api_type in api_types:
                api_types[api_type].append(key)

        # 生成最终字符串
        result = f"----共收录了{len(self.apis)}个API功能----\n\n"
        for api_type in api_types:
            if api_types[api_type]:
                result += f"【{api_type}】{len(api_types[api_type])}个：\n"
                for key in api_types[api_type]:
                    result += f"{key}、"
            result += "\n\n"

        return result.strip()

    def get_detail(self, api_name: str):
        """查看API功能的详细信息"""
        api_info = self.apis.get(api_name)
        if not api_info:
            return "API功能不存在"
        # 构造参数字符串
        params = api_info.get("params", {})
        params_list = [
            f"{key}={value}" if value is not None and value != "" else key
            for key, value in params.items()
        ]
        params_str = ",".join(params_list) if params_list else "无"

        return (
            f"API触发词：{api_info.get('keyword') or '无'}\n"
            f"请求地址：{api_info.get('url') or '无'}\n"
            f"数据类型：{api_info.get('type') or '无'}\n"
            f"所需参数：{params_str}\n"
            f"解析路径：{api_info.get('target') or '无'}"
        )


    @staticmethod
    def from_detail_str(detail: str) -> dict:
        """
        将 get_detail 的字符串逆向解析为 API 功能字典
        """
        api_info = {}

        lines = detail.splitlines()
        for line in lines:
            if line.startswith("API触发词：") or line.startswith("api匹配词："):
                kw = line.replace("API触发词：", "").replace("api匹配词：", "").strip()
                if kw == "无":
                    api_info["keyword"] = []
                else:
                    # 如果 kw 是形如 "['xxx']" 的字符串，先转回 list
                    if (kw.startswith("[") and kw.endswith("]")):
                        try:
                            parsed = ast.literal_eval(kw)
                            if isinstance(parsed, list):
                                api_info["keyword"] = parsed
                            else:
                                api_info["keyword"] = [kw]
                        except Exception:
                            api_info["keyword"] = [kw]
                    else:
                        # 普通逗号分隔
                        api_info["keyword"] = [k.strip() for k in kw.split(",")]

            elif line.startswith("请求地址：") or line.startswith("api地址："):
                url = line.replace("请求地址：", "").replace("api地址：", "").strip()
                api_info["url"] = "" if url == "无" else url

            elif line.startswith("数据类型：") or line.startswith("api类型："):
                api_type = line.replace("数据类型：", "").replace("api类型：", "").strip()
                api_info["type"] = "" if api_type == "无" else api_type

            elif line.startswith("所需参数："):
                params_str = line.replace("所需参数：", "").strip()
                if params_str == "无":
                    api_info["params"] = {}
                else:
                    params = {}
                    for kv in params_str.split(","):
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            params[k.strip()] = v.strip()
                        else:
                            params[kv.strip()] = ""
                    api_info["params"] = params

            elif line.startswith("解析路径："):
                target = line.replace("解析路径：", "").strip()
                api_info["target"] = "" if target == "无" else target

        return api_info

