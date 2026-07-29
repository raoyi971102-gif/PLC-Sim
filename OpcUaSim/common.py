"""
XUSE OPC UA 仿真项目 —— 公共工具模块
==========================================================================
被 server.py 与 handshake_agent.py 共用：
  - 日志初始化 setup_logging()
  - CSV 数据结构 NodeDef 与 load_csv()
  - 数据类型映射表 VTYPE_MAP / DEFAULT_MAP
  - 握手节点分类正则 _HS_PATTERNS + parse_suffix()
  - Type-A 位置节点匹配 _POS_PATTERNS + match_pos_node()
  - YAML 配置读取 load_yaml()
"""

from __future__ import annotations

import csv
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from opcua import ua

try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover
    yaml = None


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
def setup_logging(name: str = "XUSE") -> logging.Logger:
    """初始化日志格式；opcua 库自身噪声降为 WARNING。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("opcua").setLevel(logging.WARNING)
    return logging.getLogger(name)


log = logging.getLogger("XUSE-common")


# ---------------------------------------------------------------------------
# 可移植路径
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
BUILTIN_DEMO_CSV = PROJECT_ROOT / "data" / "demo_variables.csv"


def default_csv_path() -> Path:
    """返回默认变量表；可用 OPCUASIM_CSV 环境变量覆盖。"""
    configured = os.environ.get("OPCUASIM_CSV")
    if configured:
        return Path(configured).expanduser().resolve()
    return BUILTIN_DEMO_CSV


# ---------------------------------------------------------------------------
# 数据类型映射（CSV → opcua）
# ---------------------------------------------------------------------------
VTYPE_MAP: Dict[str, ua.VariantType] = {
    "BOOLEAN": ua.VariantType.Boolean,
    "INT16":   ua.VariantType.Int16,
    "INT32":   ua.VariantType.Int32,
    "FLOAT":   ua.VariantType.Float,
    "STRING":  ua.VariantType.String,
}
DEFAULT_MAP: Dict[str, Any] = {
    "BOOLEAN": False,
    "INT16":   0,
    "INT32":   0,
    "FLOAT":   0.0,
    "STRING":  "",
}


# ---------------------------------------------------------------------------
# 节点定义
# ---------------------------------------------------------------------------
@dataclass
class NodeDef:
    name_cn: str          # 中文名，来自 CSV `Name`
    name_en: str          # 英文名，来自 CSV `EnglishName`
    node_type: str        # VARIABLE / METHOD
    data_type: str        # BOOLEAN / INT16 / ...
    node_id: str          # ns=4;s=uniab|<name_cn>


def load_csv(path: Path) -> List[NodeDef]:
    """读取 CSV，兼容 UTF-8 / GBK / BOM，只保留 VARIABLE 节点。"""
    encodings = ("utf-8-sig", "utf-8", "gbk", "gb18030")
    text: Optional[str] = None
    for enc in encodings:
        try:
            text = path.read_text(encoding=enc)
            log.info("CSV 使用编码读取成功: %s (%s)", enc, path)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise RuntimeError(f"无法用常见编码读取 CSV: {path}")

    reader = csv.DictReader(text.splitlines())
    nodes: List[NodeDef] = []
    seen: set = set()
    for row in reader:
        name_cn = (row.get("Name") or "").strip()
        if not name_cn or name_cn in seen:
            continue
        seen.add(name_cn)

        name_en = (row.get("EnglishName") or "").strip()
        ntype = (row.get("NodeType") or "VARIABLE").strip().upper()
        dtype = (row.get("DataType") or "BOOLEAN").strip().upper()
        nid = (row.get("NodeId") or "").strip()

        if ntype != "VARIABLE":
            log.debug("跳过非 VARIABLE 节点: %s", name_cn)
            continue
        if dtype not in VTYPE_MAP:
            log.warning("未知数据类型 %r（%s），跳过", dtype, name_cn)
            continue

        if not nid:
            nid = f"ns=4;s=uniab|{name_cn}"

        nodes.append(NodeDef(name_cn, name_en, ntype, dtype, nid))
    log.info("CSV 解析完成：共 %d 个 VARIABLE 节点", len(nodes))
    return nodes


def load_csvs(csv_paths: List[Path]) -> List[NodeDef]:
    """批量加载并跨表去重（后加载的同名节点会被跳过）。"""
    node_defs: List[NodeDef] = []
    seen: set = set()
    for cp in csv_paths:
        for nd in load_csv(cp):
            if nd.name_cn in seen:
                log.debug("跨 CSV 去重: %s", nd.name_cn)
                continue
            seen.add(nd.name_cn)
            node_defs.append(nd)
    log.info("合并后共 %d 个 VARIABLE 节点", len(node_defs))
    return node_defs


# ---------------------------------------------------------------------------
# 握手分类器 —— 根据后缀识别节点角色
# ---------------------------------------------------------------------------
# CSV 里的中文命名是无下划线连接的（例如 "工站初始化"、"机械臂初始化_1"），
# 通道号才用下划线；因此不能用简单的 endswith("_初始化") 匹配。
_HS_PATTERNS = [
    # role_key,  regex,                                              kind,        default_delay_ms
    ("param_R",  re.compile(r"^(.+?)参数下发完成(_\d+)?$"),         "param_C",   200),
    ("param_W",  re.compile(r"^(.+?)参数下发(_\d+)?$"),             "param_C",   200),
    ("init_R",   re.compile(r"^(.+?)初始化完成(_\d+)?$"),           "init_D",    600),
    ("init_W",   re.compile(r"^(.+?)初始化(_\d+)?$"),               "init_D",    600),
    ("proc_R",   re.compile(r"^(.+?)加工完成(_\d+)?$"),             "process_B", 1500),
    ("proc_W",   re.compile(r"^(.+?)开始加工(_\d+)?$"),             "process_B", 1500),
    ("action_R", re.compile(r"^(.+?)动作完成(_\d+)?$"),             "action_A",  1200),
    ("action_W", re.compile(r"^(.+?)动作触发(_\d+)?$"),             "action_A",  1200),
    ("req",      re.compile(r"^(.+?)请求加工(_\d+)?$"),             "process_B", 0),
]


def parse_suffix(name_cn: str):
    """返回 (base, role, kind, delay_ms) 或 None。base 会带上通道后缀 (如 '机械臂_1')。"""
    for role_key, pat, kind, delay in _HS_PATTERNS:
        m = pat.match(name_cn)
        if not m:
            continue
        base = m.group(1) + (m.group(2) or "")
        if role_key.endswith("_R"):
            role = "R"
        elif role_key == "req":
            role = "REQ"
        else:
            role = "W"
        return (base, role, kind, delay)
    return None


# Type-A 编码触发的联动节点
_POS_PATTERNS = {
    "target_pos_node":   re.compile(r"^(.+?)目标位置代码(_\d+)?$"),
    "target_pick_node":  re.compile(r"^(.+?)目标取放代码(_\d+)?$"),
    "current_pos_node":  re.compile(r"^(.+?)当前位置(_\d+)?$"),
    "current_pick_node": re.compile(r"^(.+?)当前取放料(_\d+)?$"),
}


def match_pos_node(name_cn: str, hs_base: str) -> Optional[str]:
    """如果 name_cn 属于 hs_base 对应的位置节点，返回属性名；否则 None。"""
    for attr, pat in _POS_PATTERNS.items():
        m = pat.match(name_cn)
        if not m:
            continue
        base = m.group(1) + (m.group(2) or "")
        if base == hs_base:
            return attr
    return None


# "_占位 / _空闲" 类节点（Type-B 前置条件的仿真开关）
OCC_RE = re.compile(r"(占位|空闲)(_\d+)?$")


# ---------------------------------------------------------------------------
# YAML 配置
# ---------------------------------------------------------------------------
def load_yaml(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    if yaml is None:
        log.warning("未安装 PyYAML，忽略 --config")
        return {}
    p = Path(path)
    if not p.exists():
        log.warning("配置文件不存在: %s", p)
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
