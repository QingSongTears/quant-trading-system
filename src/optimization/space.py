"""
参数空间定义
=============

支持:
- Float(name, low, high)  : 连续浮点
- Int(name, low, high)    : 离散整数
- Categorical(name, opts) : 离散选项
- ParamSpace.from_yaml()   : 从 YAML 加载

YAML 格式:
```yaml
- name: sector_cap_pct
  type: float
  low: 0.10
  high: 0.40
  step: 0.05        # 可选, 用于网格搜索离散化

- name: max_count
  type: int
  low: 2
  high: 6

- name: weight_mode
  type: categorical
  options: [equal, volatility, atr]

- name: hold_days
  type: int
  low: 5
  high: 30
```
"""
from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml


class Param(ABC):
    """参数基类"""
    name: str

    @abstractmethod
    def grid_points(self) -> list:
        """网格搜索时返回离散取值列表"""

    @abstractmethod
    def to_optuna(self):
        """转 Optuna 空间"""

    @abstractmethod
    def to_skopt(self):
        """转 scikit-optimize 空间"""


@dataclass
class Float(Param):
    """连续浮点参数"""
    name: str
    low: float
    high: float
    step: float | None = None  # 网格离散化步长

    def grid_points(self) -> list[float]:
        if self.step is None:
            # 默认 5 个均分点 (含两端)
            return [self.low + (self.high - self.low) * i / 4 for i in range(5)]
        n = int(round((self.high - self.low) / self.step))
        return [round(self.low + i * self.step, 10) for i in range(n + 1)]

    def to_optuna(self):
        import optuna
        step = self.step if self.step is not None else None
        return optuna.distributions.FloatDistribution(
            self.low, self.high, step=step, log=False,
        )

    def to_skopt(self):
        from skopt.space import Real
        return Real(self.low, self.high, name=self.name)


@dataclass
class Int(Param):
    """离散整数参数"""
    name: str
    low: int
    high: int
    step: int = 1

    def grid_points(self) -> list[int]:
        return list(range(self.low, self.high + 1, self.step))

    def to_optuna(self):
        import optuna
        return optuna.distributions.IntDistribution(
            self.low, self.high, step=self.step, log=False,
        )

    def to_skopt(self):
        from skopt.space import Integer
        return Integer(self.low, self.high, name=self.name)


@dataclass
class Categorical(Param):
    """离散选项参数"""
    name: str
    options: list[Any]

    def grid_points(self) -> list:
        return list(self.options)

    def to_optuna(self):
        import optuna
        return optuna.distributions.CategoricalDistribution(self.options)

    def to_skopt(self):
        from skopt.space import Categorical as SkCategorical
        return SkCategorical(self.options, name=self.name)


def _parse_param(node: dict) -> Param:
    """从 YAML 节点解析参数"""
    t = node.get("type", "float").lower()
    name = node["name"]
    if t == "float":
        return Float(name=name, low=float(node["low"]), high=float(node["high"]),
                     step=node.get("step"))
    elif t in ("int", "integer"):
        return Int(name=name, low=int(node["low"]), high=int(node["high"]),
                   step=node.get("step", 1))
    elif t in ("cat", "categorical"):
        return Categorical(name=name, options=list(node["options"]))
    raise ValueError(f"未知参数类型: {t}")


@dataclass
class ParamSpace:
    """参数空间"""
    params: list[Param] = field(default_factory=list)

    def add(self, *params: Param) -> "ParamSpace":
        self.params.extend(params)
        return self

    def __iter__(self):
        return iter(self.params)

    def __len__(self):
        return len(self.params)

    def names(self) -> list[str]:
        return [p.name for p in self.params]

    def grid_iter(self) -> Iterable[dict[str, Any]]:
        """笛卡尔积生成参数组合"""
        keys = self.names()
        for combo in itertools.product(*(p.grid_points() for p in self.params)):
            yield dict(zip(keys, combo))

    def to_optuna(self) -> dict:
        """返回 Optuna 空间 dict"""
        return {p.name: p.to_optuna() for p in self.params}

    def to_skopt(self) -> list:
        """返回 scikit-optimize 空间列表"""
        return [p.to_skopt() for p in self.params]

    def to_yaml(self, path: str | Path) -> None:
        """序列化到 YAML"""
        nodes = []
        for p in self.params:
            node = {"name": p.name}
            if isinstance(p, Float):
                node["type"] = "float"
                node["low"], node["high"] = p.low, p.high
                if p.step is not None:
                    node["step"] = p.step
            elif isinstance(p, Int):
                node["type"] = "int"
                node["low"], node["high"], node["step"] = p.low, p.high, p.step
            elif isinstance(p, Categorical):
                node["type"] = "categorical"
                node["options"] = p.options
            nodes.append(node)
        Path(path).write_text(yaml.safe_dump(nodes, allow_unicode=True), encoding="utf-8")

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ParamSpace":
        """从 YAML 加载"""
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"{path} 应为参数列表")
        return cls(params=[_parse_param(n) for n in data])

    @classmethod
    def from_dict(cls, spec: list[dict]) -> "ParamSpace":
        """从 dict 列表加载"""
        return cls(params=[_parse_param(n) for n in spec])