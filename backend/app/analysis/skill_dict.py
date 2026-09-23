"""技能词典：关键词匹配的"事实来源"。

**为什么手写词典而不是上分词器**：
- 项目没有分词依赖（jieba 会带一堆模型文件），而 JD/简历里的关键词高度集中在技术名词上；
- 手写词典**可控**：哪些算一项技能、别名怎么归一、边界怎么切，都能被测试钉死；
  分词器的切词结果会随版本变化，导致"同一个简历昨天 78 分今天 71 分"这种无法解释的漂移。

覆盖编程语言 / 框架 / 数据库 / 中间件 / 云与运维 / 数据与 AI / 工程实践 / 通用能力八组。
"""

from __future__ import annotations

import re

#: 分组只是为了让报告好读（"你缺的是数据库类技能"比一堆散词有用）
SKILL_GROUPS: dict[str, tuple[str, ...]] = {
    "language": (
        "Python",
        "Java",
        "Go",
        "C++",
        "C#",
        "JavaScript",
        "TypeScript",
        "PHP",
        "Ruby",
        "Rust",
        "Scala",
        "Kotlin",
        "Shell",
        "SQL",
        "Lua",
    ),
    "framework": (
        "FastAPI",
        "Django",
        "Flask",
        "Tornado",
        "Spring Boot",
        "Spring Cloud",
        "Spring",
        "MyBatis",
        "Hibernate",
        "React",
        "Vue",
        "Angular",
        "Node.js",
        "Express",
        "Gin",
        "gRPC",
        "Dubbo",
        "Celery",
        "Scrapy",
    ),
    "database": (
        "MySQL",
        "PostgreSQL",
        "Oracle",
        "MongoDB",
        "Redis",
        "Elasticsearch",
        "ClickHouse",
        "HBase",
        "Cassandra",
        "TiDB",
        "SQLite",
        "Neo4j",
        "SQL Server",
    ),
    "middleware": (
        "Kafka",
        "RabbitMQ",
        "RocketMQ",
        "ActiveMQ",
        "Pulsar",
        "Nginx",
        "Zookeeper",
        "etcd",
        "Netty",
        "Tomcat",
    ),
    "cloud": (
        "Docker",
        "Kubernetes",
        "AWS",
        "阿里云",
        "腾讯云",
        "华为云",
        "Azure",
        "Jenkins",
        "GitLab CI",
        "Terraform",
        "Prometheus",
        "Grafana",
        "ELK",
        "Linux",
        "CI/CD",
        "Ansible",
    ),
    "data_ai": (
        "Spark",
        "Flink",
        "Hadoop",
        "Hive",
        "Airflow",
        "NumPy",
        "Pandas",
        "PyTorch",
        "TensorFlow",
        "LLM",
        "RAG",
        "机器学习",
        "深度学习",
        "推荐系统",
        "数据仓库",
        "ETL",
        "BI",
        "向量检索",
    ),
    "practice": (
        "高并发",
        "高可用",
        "分布式",
        "微服务",
        "性能优化",
        "架构设计",
        "领域驱动",
        "单元测试",
        "自动化测试",
        "代码审查",
        "敏捷开发",
        "DevOps",
        "监控告警",
        "灰度发布",
        "容量规划",
        "限流",
        "熔断",
        "降级",
        "缓存",
        "消息队列",
        "分库分表",
        "幂等",
        "链路追踪",
        "负载均衡",
        "重构",
    ),
    "soft": (
        "团队协作",
        "沟通能力",
        "项目管理",
        "需求分析",
        "跨部门协作",
        "带团队",
        "技术选型",
        "文档编写",
        "英语",
        "抗压能力",
        "主动性",
    ),
}

#: 别名 → 标准名（JD 里写法五花八门，不归一的话"缺 Go"会误报成两个词）
ALIASES: dict[str, str] = {
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "golang": "Go",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "pg": "PostgreSQL",
    "es": "Elasticsearch",
    "elastic search": "Elasticsearch",
    "mq": "消息队列",
    "message queue": "消息队列",
    "kafkaesque": "Kafka",
    "springboot": "Spring Boot",
    "spring cloud": "Spring Cloud",
    "mybatis": "MyBatis",
    "nodejs": "Node.js",
    "node": "Node.js",
    "vue.js": "Vue",
    "vue3": "Vue",
    "react.js": "React",
    "ts": "TypeScript",
    "js": "JavaScript",
    "py": "Python",
    "cplusplus": "C++",
    "csharp": "C#",
    "sqlserver": "SQL Server",
    "tidb": "TiDB",
    "ci cd": "CI/CD",
    "cicd": "CI/CD",
    "持续集成": "CI/CD",
    "大模型": "LLM",
    "检索增强": "RAG",
    "ddd": "领域驱动",
    "code review": "代码审查",
    "重构": "重构",
    "machine learning": "机器学习",
    "深度学习": "深度学习",
    "容器化": "Docker",
    "容器编排": "Kubernetes",
    "微服务体系": "微服务",
    "分表分库": "分库分表",
}

#: 组内技能 → 组名（反向索引）
_SKILL_TO_GROUP: dict[str, str] = {
    skill: group for group, skills in SKILL_GROUPS.items() for skill in skills
}

#: 全角字符（ＡＢＣ１２３）不会被英文边界匹配命中，这是 ATS 关键词失效的常见原因
FULLWIDTH = re.compile(r"[Ａ-Ｚａ-ｚ０-９]")


def _has_latin(text: str) -> bool:
    return any("a" <= char.lower() <= "z" for char in text)


def normalize(word: str) -> str:
    """把别名归一成标准技能名（未知词原样返回）。"""
    cleaned = (word or "").strip()
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if lowered in ALIASES:
        return ALIASES[lowered]
    for skill in _SKILL_TO_GROUP:
        if skill.lower() == lowered:
            return skill
    return cleaned


def all_skills() -> list[str]:
    return list(_SKILL_TO_GROUP)


def skill_group(skill: str) -> str:
    return _SKILL_TO_GROUP.get(skill, "other")


def group_label(group: str) -> str:
    return {
        "language": "编程语言",
        "framework": "框架",
        "database": "数据库",
        "middleware": "中间件",
        "cloud": "云与运维",
        "data_ai": "数据与 AI",
        "practice": "工程实践",
        "soft": "通用能力",
        "other": "其他",
    }.get(group, group)


def _pattern_for(skill: str) -> re.Pattern[str]:
    """英文技能用词边界，中文技能用子串。

    词边界不是洁癖：`Java` 不加边界会命中 `JavaScript`，
    于是"候选人有 Java 经验"这种假阳性会直接影响匹配分。
    """
    if _has_latin(skill):
        escaped = re.escape(skill)
        # 特殊字符（C++ / C# / CI/CD）用更宽的自定义边界
        if any(symbol in skill for symbol in "++#/"):
            return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9\+#])", re.IGNORECASE)
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(re.escape(skill))


#: 预编译（技能数固定，避免每次调用重复编译）
_PATTERNS: dict[str, re.Pattern[str]] = {skill: _pattern_for(skill) for skill in _SKILL_TO_GROUP}
_ALIAS_PATTERNS: dict[str, re.Pattern[str]] = {
    alias: _pattern_for(alias) for alias in ALIASES
}


def find_skills(text: str) -> dict[str, list[str]]:
    """在一段文本里找出所有技能，返回 {标准技能名: [命中的原文写法…]}。

    同时匹配标准名与别名，所以"用 k8s 也会算作 Kubernetes"。
    """
    content = text or ""
    if not content:
        return {}

    hits: dict[str, list[str]] = {}

    for skill, pattern in _PATTERNS.items():
        found = pattern.findall(content)
        if found:
            hits.setdefault(skill, []).extend(found)

    for alias, pattern in _ALIAS_PATTERNS.items():
        if not pattern.search(content):
            continue
        standard = ALIASES[alias]
        # 别名命中的原文写法记到标准技能名下（去重后由调用方计数）
        hits.setdefault(standard, []).append(alias)

    return hits
