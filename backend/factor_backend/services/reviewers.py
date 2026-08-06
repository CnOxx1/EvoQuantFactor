from __future__ import annotations

"""评审角色表。Mock/Live 实现见 agents.py。"""

ROLES = [
    ("R1", "量化研究员", {"Implementability": 1.1, "Robustness": 1.05}),
    ("R2", "权益基金经理", {"Edge": 1.1, "Tradability": 1.05}),
    ("R3", "风控官", {"RiskControl": 1.15, "Robustness": 1.05}),
    ("R4", "卖方策略分析师", {"Logic": 1.1, "Novelty": 1.05}),
    ("R5", "数据工程师", {"Implementability": 1.2}),
    ("R6", "买方研究总监", {"Novelty": 1.1, "Edge": 1.05}),
]
