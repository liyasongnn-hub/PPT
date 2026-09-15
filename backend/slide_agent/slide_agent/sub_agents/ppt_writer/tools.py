#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date  : 2025/6/20 10:02
# @File  : tools.py.py
# @Author: johnson
# @Contact : github: johnson7788
# @Desc  : 搜索图片，用于PPT的配图

import re
import os
import time
import logging
import httpx
from datetime import datetime
import random
import hashlib
from pathlib import Path
from google.adk.tools import ToolContext
import requests
from urllib.parse import quote
import json
from typing import List, Dict, Any
from .weixin_search import sogou_weixin_search,get_real_url,get_article_content

logger = logging.getLogger(__name__)

async def SearchImage(query: str, count: int = 1, tool_context: ToolContext = None) -> List[Dict[str, Any]]:
    """
    根据关键词搜索对应的图片，使用Pexels API
    :param query: 搜索关键词
    :param count: 返回图片数量，默认1张
    :param tool_context: 工具上下文
    :return: 图片信息列表
    """
    try:
        # 从环境变量获取Pexels API密钥
        pexels_api_key = os.getenv("PEXELS_API_KEY")
        if not pexels_api_key:
            # 如果没有API密钥，使用模拟数据
            return _get_simulate_images(query, count)
        
        # 构建Pexels API请求
        headers = {
            "Authorization": pexels_api_key
        }
        
        # 对查询词进行URL编码
        encoded_query = quote(query)
        url = f"https://api.pexels.com/v1/search?query={encoded_query}&per_page={min(count, 80)}&orientation=landscape"
        
        logger.info("开始图片搜索 query_chars=%s", len(query))
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        photos = data.get("photos", [])
        
        if not photos:
            logger.info("图片搜索无结果，使用备用数据")
            return _get_simulate_images(query, count)
        
        # 转换为前端需要的格式
        image_results = []
        for photo in photos[:count]:
            image_info = {
                "id": photo.get("id", random.randint(100000, 999999)),
                "src": photo.get("src", {}).get("large2x", photo.get("src", {}).get("large", "")),
                "width": photo.get("width", 1920),
                "height": photo.get("height", 1080),
                "alt": photo.get("alt", query),
                "photographer": photo.get("photographer", "Unknown"),
                "url": photo.get("url", "")
            }
            image_results.append(image_info)
        
        print(f"成功搜索到 {len(image_results)} 张图片")
        return image_results
        
    except requests.exceptions.RequestException as e:
        print(f"Pexels API请求失败: {e}")
        return _get_simulate_images(query, count)
    except Exception as e:
        print(f"图片搜索出错: {e}")
        return _get_simulate_images(query, count)


def _get_simulate_images(query: str, count: int) -> List[Dict[str, Any]]:
    """
    获取模拟图片数据（当API不可用时使用）
    """
    # 根据查询词选择不同的模拟图片
    query_lower = query.lower()
    
    # 预设的图片池，按主题分类
    image_pools = {
        "technology": [
            "https://images.pexels.com/photos/3861969/pexels-photo-3861969.jpeg",
            "https://images.pexels.com/photos/3861967/pexels-photo-3861967.jpeg",
            "https://images.pexels.com/photos/3861966/pexels-photo-3861966.jpeg",
            "https://images.pexels.com/photos/3861965/pexels-photo-3861965.jpeg",
            "https://images.pexels.com/photos/3861964/pexels-photo-3861964.jpeg",
        ],
        "business": [
            "https://images.pexels.com/photos/3183150/pexels-photo-3183150.jpeg",
            "https://images.pexels.com/photos/3183153/pexels-photo-3183153.jpeg",
            "https://images.pexels.com/photos/3183154/pexels-photo-3183154.jpeg",
            "https://images.pexels.com/photos/3183155/pexels-photo-3183155.jpeg",
            "https://images.pexels.com/photos/3183156/pexels-photo-3183156.jpeg",
        ],
        "nature": [
            "https://images.pexels.com/photos/3225517/pexels-photo-3225517.jpeg",
            "https://images.pexels.com/photos/3225518/pexels-photo-3225518.jpeg",
            "https://images.pexels.com/photos/3225519/pexels-photo-3225519.jpeg",
            "https://images.pexels.com/photos/3225520/pexels-photo-3225520.jpeg",
            "https://images.pexels.com/photos/3225521/pexels-photo-3225521.jpeg",
        ],
        "abstract": [
            "https://images.pexels.com/photos/3255761/pexels-photo-3255761.jpeg",
            "https://images.pexels.com/photos/3255762/pexels-photo-3255762.jpeg",
            "https://images.pexels.com/photos/3255763/pexels-photo-3255763.jpeg",
            "https://images.pexels.com/photos/3255764/pexels-photo-3255764.jpeg",
            "https://images.pexels.com/photos/3255765/pexels-photo-3255765.jpeg",
        ]
    }
    
    # 根据查询词选择最匹配的图片池
    selected_pool = "abstract"  # 默认
    for keyword, pool in image_pools.items():
        if keyword in query_lower:
            selected_pool = keyword
            break
    
    # 从选中的池中随机选择图片
    pool_images = image_pools[selected_pool]
    selected_images = random.sample(pool_images, min(count, len(pool_images)))
    
    # 如果需要的数量超过池中的图片，重复选择
    while len(selected_images) < count:
        selected_images.extend(random.sample(pool_images, min(count - len(selected_images), len(pool_images))))
    
    # 转换为标准格式
    image_results = []
    for i, src in enumerate(selected_images[:count]):
        image_info = {
            "id": random.randint(100000, 999999) + i,
            "src": src,
            "width": 1920,
            "height": 1080,
            "alt": f"{query} image {i+1}",
            "photographer": "Pexels",
            "url": src
        }
        image_results.append(image_info)
    
    return image_results


async def DocumentSearch(
    keyword: str, number: int,
    tool_context: ToolContext,
):
    """
    根据关键词搜索文档
    :param keyword: str, 搜索的相关文档的关键词
    :return: 返回每篇文档数据
    """
    agent_name = tool_context.agent_name
    logger.info("Agent %s 调用文档搜索 keyword_chars=%s", agent_name, len(keyword))
    metadata = tool_context.state.get("metadata", {})
    if metadata is None:
        metadata = {}
    start_time = time.time()
    results = sogou_weixin_search(keyword)
    if not results:
        return f"没有搜索到{keyword}相关的文章"
    articles = []
    results = results[:number]
    for every_result in results:
        sougou_link = every_result["link"]
        real_url = get_real_url(sougou_link)
        # referer：请求来源
        content = get_article_content(real_url, referer=sougou_link)
        article = {
            "title": every_result["title"],
            "publish_time": every_result["publish_time"],
            "real_url": real_url,
            "content": content
        }
        articles.append(article)
    end_time = time.time()
    logger.info("文档搜索完成 results=%s elapsed_seconds=%.2f", len(articles), end_time - start_time)
    metadata["tool_document_ids"] = articles
    tool_context.state["metadata"] = metadata
    return articles

def KnowledgeBaseSearch(keyword: str, tool_context: ToolContext):
    """
    根据关键词搜索文档库
    :param keyword: str, 搜索的相关文档的关键词
    :return: 返回每篇文档数据
    """
    topk = 5  # 搜索前5条结果
    metadata = tool_context.state.get("metadata", {})
    # 就是对应用户上传PDF文件
    user_id = metadata.get("user_id", 999)
    if not user_id:
        user_id = 999
    logger.info("调用知识库搜索 user_id=%s query_chars=%s topk=%s", user_id, len(keyword), topk)
    PERSONAL_DB = os.environ.get('PERSONAL_DB', '')
    assert PERSONAL_DB, "PERSONAL_DB is not set"
    url = f"{PERSONAL_DB}/search"
    # 正确的请求数据格式
    data = {
        "userId": user_id,
        "query": keyword,
        "keyword": "",  # 关键词匹配，是否需要强制包含一些关键词
        "topk": topk
    }
    headers = {'content-type': 'application/json'}
    internal_token = os.environ.get('PERSONALDB_INTERNAL_TOKEN', '').strip()
    if internal_token:
        headers['X-Internal-Token'] = internal_token
    try:
        # 发送POST请求
        response = httpx.post(url, json=data, headers=headers, timeout=20.0, trust_env=False)

        # 检查HTTP状态码
        response.raise_for_status()
        assert response.status_code == 200, f"{PERSONAL_DB}搜索知识库报错"

        # 解析返回的JSON数据
        result = response.json()
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])
        data = {"documents": documents, "metadatas": metadatas}
        logger.info("知识库搜索成功 status=%s document_groups=%s", response.status_code, len(documents))
        return True, data
    except Exception as e:
        print(f"{PERSONAL_DB}搜索知识库报错: {e}")
        return False, f"{PERSONAL_DB}搜索知识库报错: {str(e)}"

if __name__ == '__main__':
    import asyncio
    
    async def test():
        # 测试搜索功能
        result = await SearchImage("technology", 3)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    
    asyncio.run(test())
