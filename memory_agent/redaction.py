"""redaction.py —— 敏感信息脱敏的单源实现。

用途：记忆写入前的确定性脱敏（统一入口 upsert_memory / replace_memory_result），
以及会话调试包解析时的展示脱敏（debug_bundle）。两处共用同一套规则，避免漂移。

策略（与项目不变量一致）：
- 只处理**明确高危**的凭据格式，宁可漏也不误伤正文——脱敏不是内容审查。
- 公网 IP 打码；回环/内网/链路本地/CGNAT 保留（127.0.0.1:32608 这类本机地址是有用线索）。
- 幂等：已打码的文本再次 sanitize 不产生新变更（全文替换语义下会重复经过这里）。
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, NamedTuple


class Finding(NamedTuple):
    """一条脱敏命中。"""

    kind: str
    original: str
    masked: str


# 凭据类：按“格式特征”匹配，不按可疑词匹配（避免把正常说明文字打烂）。
# 前缀型统一保留前 3 位可读前缀，其余替换为 ***，便于人工辨认是哪一类密钥。
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("npm-token", re.compile(r"npm_[A-Za-z0-9]{20,}"), "npm_***"),
    ("api-key", re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "sk-***"),
    ("github-token", re.compile(r"ghp_[A-Za-z0-9]{20,}"), "ghp_***"),
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA***"),
    ("volc-key", re.compile(r"ark-[0-9a-f]{8}-[0-9a-f-]{8,}"), "ark-***"),
    ("bearer", re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"), r"\1***"),
    (
        "assigned-credential",
        re.compile(
            r"(?i)((?:api[_-]?key|access[_-]?key|secret|token|password|passwd)\s*[\"']?\s*[:=]\s*[\"']?)"
            r"([A-Za-z0-9._\-/+]{12,})"
        ),
        r"\1***",
    ),
    (
        "private-key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "<已脱敏私钥块>",
    ),
]

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{1,4}\b")

# 这些网段不算敏感：本机、内网、链路本地、运营商级 NAT、未指定
_KEEP_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("0.0.0.0/32"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def is_sensitive_ip(text: str) -> bool:
    """公网 IP 返回 True；回环/内网等保留地址返回 False。

    只按显式保留网段判断，**不用 addr.is_private**：Python 把 IANA 特殊用途段
    （203.0.113.0/24、198.51.100.0/24、192.0.2.0/24 等文档/基准段）也算 private，
    用它会把这些地址当内网放过去（实测踩过）。"""
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return False
    if addr.is_loopback or addr.is_unspecified:
        return False
    return not any(addr in net for net in _KEEP_NETWORKS)


def _mask_ip(text: str) -> str:
    addr = ipaddress.ip_address(text)
    if isinstance(addr, ipaddress.IPv4Address):
        return ".".join(text.split(".")[:2]) + ".x.x"
    return text.split(":")[0] + ":xxxx"


def sanitize(text: str) -> tuple[str, list[Finding]]:
    """返回 (脱敏后文本, 命中列表)。幂等：已脱敏文本再次调用命中为空。"""
    if not text:
        return text, []
    findings: list[Finding] = []
    out = text

    for kind, pattern, repl in _SECRET_PATTERNS:
        def _sub(match: re.Match[str], kind: str = kind, repl: str = repl) -> str:
            masked = match.expand(repl)
            findings.append(Finding(kind, match.group(0), masked))
            return masked

        out = pattern.sub(_sub, out)

    def _ip_sub(match: re.Match[str]) -> str:
        original = match.group(0)
        if not is_sensitive_ip(original):
            return original
        masked = _mask_ip(original)
        findings.append(Finding("public-ip", original, masked))
        return masked

    out = _IPV4.sub(_ip_sub, out)
    out = _IPV6.sub(_ip_sub, out)
    return out, findings


def scan(text: str) -> list[Finding]:
    """只检测不改写（用于写入前告知模型）。"""
    return sanitize(text)[1]


def describe(findings: list[Finding]) -> str:
    """人类可读的命中摘要，供工具返回/日志展示。"""
    if not findings:
        return ""
    kinds: dict[str, int] = {}
    for finding in findings:
        kinds[finding.kind] = kinds.get(finding.kind, 0) + 1
    parts = [f"{kind}×{count}" for kind, count in sorted(kinds.items())]
    return "、".join(parts)


def redact_for_display(text: str) -> str:
    """展示层脱敏（调试包输出用），丢弃命中明细。"""
    return sanitize(text)[0]


def findings_to_json(findings: list[Finding]) -> list[dict[str, Any]]:
    """审计/调试用的结构化命中（不含原文，只留类型与长度）。"""
    return [
        {"kind": f.kind, "masked": f.masked, "original_len": len(f.original)}
        for f in findings
    ]
