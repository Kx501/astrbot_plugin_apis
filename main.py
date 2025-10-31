import os
from pathlib import Path
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api.provider import ProviderRequest, LLMResponse
import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star.filter.event_message_type import EventMessageType
from astrbot.core.message.components import (
    BaseMessageComponent,
    Image,
    Plain,
    Record,
    Video,
)
from .core.local import LocalDataManager
from .core.api_manager import APIManager
from .core.utils import get_nickname
from .core.request import RequestManager


@register("astrbot_plugin_apis_fork", "Kx501", "API聚合插件", "...", "...")
class APIsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        # 启用的 API 类型（直接从列表获取）
        self.enable_api_type = config.get("enabled_types", ["text", "image", "video", "audio"])
        # 本地数据存储路径
        self.local_data_dir = StarTools.get_data_dir("astrbot_plugin_apis_fork")
        # api数据文件
        self.system_api_file = Path(__file__).parent / "system_api.json"
        self.apis_file = self.local_data_dir / "apis.json"

    async def initialize(self):
        self.local = LocalDataManager(self.local_data_dir)
        self.api = APIManager(self.system_api_file, self.apis_file)
        self.apis_names = self.api.get_apis_names()
        self.web = RequestManager(self.conf, self.api)

    @staticmethod
    async def data_to_chain(
        api_type: str, text: str | None = "", path: str | Path | None = ""
    ) -> list[BaseMessageComponent]:
        """根据数据类型构造消息链"""
        chain = []
        if api_type == "text" and text:
            chain = [Plain(text)]

        elif api_type == "image" and path:
            chain = [Image.fromFileSystem(str(path))]

        elif api_type == "video" and path:
            chain = [Video.fromFileSystem(str(path))]

        elif api_type == "audio" and path:
            chain = [Record.fromFileSystem(str(path))]

        return chain  # type: ignore

    async def _supplement_args(self, event: AstrMessageEvent, args: list, params: dict):
        """
        补充参数逻辑
        :param event: 事件对象
        :param args: 当前参数列表（可能为空）
        :param params: 参数字典
        :return: 更新后的 args 和 params
        """
        # 尝试从回复消息中提取参数
        if not args:
            reply_seg = next(
                (seg for seg in event.get_messages() if isinstance(seg, Comp.Reply)),
                None,
            )
            if reply_seg and reply_seg.chain:
                for seg in reply_seg.chain:
                    if isinstance(seg, Comp.Plain):
                        args = seg.text.strip().split(" ")
                        break

        # 如果仍未获取到参数，尝试从 @ 消息中提取昵称
        if not args:
            for seg in event.get_messages():
                if isinstance(seg, Comp.At):
                    seg_qq = str(seg.qq)
                    if seg_qq != event.get_self_id():
                        nickname = await get_nickname(event, seg_qq)
                        if nickname:
                            args.append(nickname)
                            break
        # 如果仍未获取到参数，尝试使用发送者名称作为额外参数
        if not args:
            extra_arg = event.get_sender_name()
            params = {
                key: extra_arg if not value else value for key, value in params.items()
            }

        return args, params

    @filter.command("api列表")
    async def api_list(self, event: AstrMessageEvent, api_name: str | None = None):
        """查看所有API功能列表"""
        api_info = self.api.list_api()
        yield event.plain_result(api_info)

    @filter.command("api详情")
    async def api_detail(self, event: AstrMessageEvent, api_name: str | None = None):
        """查看指定API功能的详细信息，参数为API触发词"""
        if not api_name:
            yield event.plain_result("未指定API触发词")
            return
        api_detail = self.api.get_detail(api_name)
        yield event.plain_result(api_detail)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("添加api")
    async def api_add(self, event: AstrMessageEvent):
        """添加新的API功能"""
        api_detail = event.message_str.removeprefix("添加api").strip()
        try:
            data = self.api.from_detail_str(api_detail)
            self.api.add_api(data)
            yield event.plain_result(f"添加API功能成功:\n{data}")
        except Exception as e:
            logger.error(e)
            yield event.plain_result(
                "添加API功能失败，请检查格式，务必与api详情的输出数据格式一致"
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删除api")
    async def remove_api(self, event: AstrMessageEvent, api_name: str):
        """删除指定的API功能，参数为API触发词"""
        self.api.remove_api(api_name)
        yield event.plain_result(f"已删除API功能：{api_name}")

    @filter.command("api测试")
    async def api_status(self, event: AstrMessageEvent):
        """测试所有API功能的可用性"""
        yield event.plain_result(f"正在轮询{len(self.api.apis.keys())}个API功能，请稍等...")
        abled, disabled = await self.web.batch_test_apis()
        msg = (
            f"【可用的API功能】\n{', '.join(abled)}\n\n【失效的API功能】\n{', '.join(disabled)}"
        )
        yield event.plain_result(f"{msg}")

    @filter.event_message_type(EventMessageType.ALL)
    async def match_api(self, event: AstrMessageEvent):
        """主函数"""

        # 前缀模式
        if self.conf["prefix_mode"] and not event.is_at_or_wake_command:
            return

        # 匹配api
        msgs = event.message_str.split(" ")
        api_data = self.api.match_api_by_name(msgs[0])
        if not api_data:
            return

        # 检查API功能是否被禁用
        disabled_apis = self.conf.get("disabled_apis", [])
        if api_data["name"] in disabled_apis:
            logger.debug(f"API功能 [{api_data['name']}] 已被禁用")
            return

        # 检查API站点是否被禁用
        disabled_sites = self.conf.get("disabled_sites", [])
        for url in api_data["urls"]:
            for site in disabled_sites:
                if site and url.startswith(site):
                    logger.debug(f"API站点 [{site}] 已被禁用，跳过URL: {url}")
                    return

        # 检查API数据类型是否被禁用
        if api_data["type"] not in self.enable_api_type:
            logger.debug(f"API数据类型 [{api_data['type']}] 已被禁用")
            return

        # 获取参数
        args = msgs[1:]

        # 参数补充
        args, params = await self._supplement_args(event, args, api_data["params"])

        # 生成update_params，保留params中的默认值
        update_params = {
            key: args[i] if i < len(args) else params[key]
            for i, key in enumerate(params.keys())
        }
        # 获取数据
        try:
            text, path, source = await self.call_api(api_data, update_params)
        except Exception as e:
            logger.error(f"获取数据失败: {e}")
            if self.conf.get("debug"):
                await event.send(
                    event.plain_result(f"获取数据失败 [{api_data['name']}] : {e}")
                )
            return

        # 发送消息
        chain = await self.data_to_chain(
            api_type=api_data["type"], text=text, path=path
        )
        await event.send(event.chain_result(chain))
        event.stop_event()

        # 清理临时文件
        if source == "api" and path and not self.conf.get("auto_save", True):
            os.remove(path)

    async def call_api_by_name(
        self, name: str, params: dict | None = None
    ) -> tuple[str | None, Path | None, str]:
        """
        通过触发词调用API功能（暴露给外部使用）
        :param name: API触发词
        :param params: API请求参数
        :return: (text, path, source)
                 source = "api" 表示来自网络API站点
                 source = "local" 表示来自本地缓存
        """
        api_data = self.api.match_api_by_name(name)
        logger.debug(api_data)
        if not api_data:
            return None, None, "error"

        return await self.call_api(api_data, params)

    async def call_api(
        self, api_data: dict, params: dict | None = None
    ) -> tuple[str | None, Path | None, str]:
        """
        调用API功能并返回数据
        :param api_data: API功能数据
        :param params: API请求参数
        :return: (text, path, source)
                 source = "api" 表示来自网络API站点
                 source = "local" 表示来自本地缓存
        """
        try:
            # === 外部接口调用 ===
            api_text, api_byte = await self.web.get_data(
                urls=api_data["urls"],
                params=params or api_data["params"],
                api_type=api_data["type"],
                target=api_data["target"],
            )
            if api_text or api_byte:
                saved_text, saved_path = await self.local.save_data(
                    api_type=api_data["type"],
                    path_name=api_data["name"],
                    text=api_text,
                    byte=api_byte,
                )
                return saved_text, saved_path, "api"

        except Exception as e:
            logger.warning(f"API功能调用失败 [{api_data['name']}]，尝试使用本地缓存: {e}")

        # === 本地兜底 ===
        try:
            local_text, local_path = await self.local.get_data(
                api_type=api_data["type"], path_name=api_data["name"]
            )
            return local_text, local_path, "local"
        except Exception as e:
            logger.error(f"本地缓存获取失败 [API功能: {api_data['name']}] : {e}")
            return None, None, "error"

    def _generate_api_list(self) -> str:
        """生成精简的API触发词列表，用于提示词注入（优化token消耗）"""
        api_names = []
        
        for api_name, api_data in self.api.apis.items():
            api_type = api_data.get("type", "text")
            if api_type in self.enable_api_type:
                keywords = api_data.get("keyword", [])
                if isinstance(keywords, str):
                    keywords = [keywords]
                # 只取第一个关键词作为代表
                if keywords:
                    api_names.append(keywords[0])
        
        # 返回简洁的逗号分隔列表（最多50个，避免过长）
        if api_names:
            return ",".join(api_names[:50])
        return ""

    @filter.on_llm_request()
    async def inject_api_list(self, event: AstrMessageEvent, req: ProviderRequest):
        """在系统提示词中注入API触发词列表（使用符号包裹格式）"""
        api_list = self._generate_api_list()
        if api_list:
            req.system_prompt += f"\n可用API（使用[[触发词]]调用，如[[讲个笑话]]）:{api_list}"

    @filter.on_llm_response()
    async def extract_api_from_response(self, event: AstrMessageEvent, resp: LLMResponse):
        """从LLM回复中提取[[触发词]]格式并调用对应的API功能，将结果整合到回复中"""
        import re
        
        # 获取回复文本
        reply_text = resp.completion_text if hasattr(resp, 'completion_text') else ""
        if not reply_text and hasattr(resp, 'result_chain') and resp.result_chain:
            # 从消息链中提取文本
            from astrbot.api.message_components import Plain
            text_parts = [seg.text for seg in resp.result_chain.chain if isinstance(seg, Plain)]
            reply_text = "".join(text_parts)
        
        if not reply_text:
            return
        
        # 提取所有[[...]]包裹的内容
        pattern = r'\[\[([^\]]+)\]\]'
        matches = re.findall(pattern, reply_text)
        
        if not matches:
            return
        
        # 防重复：记录已调用的API
        called_apis = set()
        api_results = []  # 存储API调用结果
        
        # 对每个提取的内容，尝试匹配并调用API
        for api_name in matches:
            api_name = api_name.strip()
            if not api_name or api_name in called_apis:
                continue
            
            # 匹配API功能（支持优先级选择）
            api_data = self.api.match_api_by_name(api_name)
            if not api_data:
                logger.debug(f"未找到匹配的API功能: {api_name}")
                continue
            
            # 检查API数据类型是否启用
            if api_data["type"] not in self.enable_api_type:
                logger.debug(f"API数据类型 [{api_data['type']}] 已被禁用")
                continue
            
            called_apis.add(api_name)
            
            try:
                # 调用API获取数据
                text, path, source = await self.call_api(api_data, None)
                
                if text or path:
                    # 添加API结果到消息链
                    api_chain = await self.data_to_chain(
                        api_type=api_data["type"], text=text, path=path
                    )
                    if api_chain:
                        api_results.extend(api_chain)
                    
                    logger.info(f"已从回复中提取并调用API功能 [{api_name}]")
            except Exception as e:
                logger.error(f"调用API功能失败 [{api_name}]: {e}")
        
        # 如果有API结果，整合到回复中
        if api_results:
            # 从回复文本中移除[[...]]标记
            cleaned_text = re.sub(pattern, '', reply_text).strip()
            
            # 确保result_chain存在
            if not resp.result_chain:
                from astrbot.api.event import MessageChain
                resp.result_chain = MessageChain()
            
            # 更新消息链：清理原始文本中的[[...]]，然后添加API结果
            if resp.result_chain.chain:
                # 更新所有Plain组件，移除[[...]]标记
                for seg in resp.result_chain.chain:
                    if isinstance(seg, Comp.Plain):
                        seg.text = cleaned_text
                        break
                else:
                    # 如果没有Plain组件，在开头添加清理后的文本
                    resp.result_chain.chain.insert(0, Comp.Plain(cleaned_text))
            else:
                # 如果chain为空，直接添加清理后的文本
                resp.result_chain.chain.append(Comp.Plain(cleaned_text))
            
            # 添加API结果到消息链
            resp.result_chain.chain.extend(api_results)
            
            # 更新completion_text（保持一致性，通过setter自动更新）
            resp.completion_text = cleaned_text

    async def terminate(self):
        """关闭会话，断开连接"""
        await self.web.terminate()
        logger.info("已关闭astrbot_plugin_apis_fork的网络连接")
