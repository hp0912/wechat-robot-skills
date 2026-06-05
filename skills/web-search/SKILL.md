---
name: web-search
description: "网页搜索技能。当用户需要联网搜索、查询最新网页信息、或让你先搜再总结时使用。"
argument-hint: "需要 query"
---

# Web Search Skill

## 描述

这是一个网页搜索技能。

技能通过浏览器能力访问下面的搜索引擎，返回网页结果：

`https://so.houhoukang.com/search?q=`

`q` 参数是搜索关键词。执行时必须对关键词进行 URL 编码后再拼接到 `q=` 后面。

## 触发条件

- 用户要求你联网搜索某个主题。
- 用户说「帮我搜一下」「查一下最新信息」「搜索这个问题」。

## 入参规范

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "搜索关键词"
    }
  },
  "required": ["query"],
  "additionalProperties": false
}
```

## 执行步骤

1. 提取用户搜索词 `query`。
2. 对 `query` 做 URL 编码，构造搜索地址：

   `https://so.houhoukang.com/search?q=<编码后的query>`

3. 使用浏览器技能访问该地址并读取网页内容。
4. 如果列表数据不够详细，无法获得有效的信息，可以继续访问搜索结果链接获取更多内容。

## 回复要求

- 返回用户需要的数据
