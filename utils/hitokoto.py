"""一言（Hitokoto）API 客户端。

不依赖 AstrBot API，可独立测试。
"""

from __future__ import annotations

from typing import Optional

import aiohttp

from astrbot.api import logger


async def get_hitokoto() -> Optional[str]:
    """获取一言句子。

    Returns:
        纯文本句子，失败时返回 None。
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://v1.hitokoto.cn/?encode=text",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                if response.status == 200:
                    return (await response.text()).strip()
                return None
    except Exception as e:
        logger.warning(f"获取一言失败: {e}")
        return None
