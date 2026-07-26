"""Sparse polynomial utilities for the QCi Dirac-3 Phase-3 entry."""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Iterable


Term = tuple[int, ...]


@dataclass
class Polynomial:
    nvars: int
    max_degree: int = 2
    binary_simplify: bool = True
    terms: dict[Term, float] = field(default_factory=dict)
    constant: float = 0.0

    def add(self, variables: Iterable[int], coefficient: float) -> None:
        coefficient = float(coefficient)
        if abs(coefficient) < 1e-12:
            return
        key = tuple(sorted(int(v) for v in variables))
        if self.binary_simplify:
            key = tuple(sorted(set(key)))
        if not key:
            self.constant += coefficient
            return
        if any(v < 0 or v >= self.nvars for v in key):
            raise IndexError(f"term {key} outside nvars={self.nvars}")
        self.max_degree = max(self.max_degree, len(key))
        self.terms[key] = self.terms.get(key, 0.0) + coefficient
        if abs(self.terms[key]) < 1e-10:
            del self.terms[key]

    def add_square_of_affine(self, coefficients: dict[int, float], offset: float,
                             weight: float) -> None:
        """Add ``weight * (offset + sum(a_i*x_i))^2`` for binary variables."""
        self.constant += weight * offset * offset
        items = list(coefficients.items())
        for i, ai in items:
            self.add([i], weight * (2.0 * offset * ai + ai * ai))
        for pos, (i, ai) in enumerate(items):
            for j, aj in items[pos + 1:]:
                self.add([i, j], 2.0 * weight * ai * aj)

    def energy(self, solution: Iterable[int | float]) -> float:
        x = [float(v) for v in solution]
        if len(x) != self.nvars:
            raise ValueError(f"expected {self.nvars} variables, got {len(x)}")
        value = self.constant
        for key, coefficient in self.terms.items():
            monomial = 1.0
            for index in key:
                monomial *= x[index]
            value += coefficient * monomial
        return float(value)

    def exact_solve_binary(self) -> dict:
        if self.nvars > 24:
            raise ValueError("audit enumerator is limited to 24 binary variables")
        best_energy = float("inf")
        best = None
        for state in product((0, 1), repeat=self.nvars):
            energy = self.energy(state)
            if energy < best_energy:
                best_energy, best = energy, list(state)
        assert best is not None
        return {
            "energy": float(best_energy),
            "x": best,
            "bitstring_lsb_first": "".join(str(v) for v in best),
            "num_states_enumerated": 2 ** self.nvars,
        }

    def exact_solve_integer(self, num_levels: Iterable[int]) -> dict:
        levels = [int(v) for v in num_levels]
        if len(levels) != self.nvars or any(v < 2 for v in levels):
            raise ValueError("num_levels must contain one integer >=2 per variable")
        total = 1
        for level in levels:
            total *= level
        if total > 5_000_000:
            raise ValueError(f"audit enumeration too large: {total} states")
        best_energy = float("inf")
        best = None
        for state in product(*(range(level) for level in levels)):
            energy = self.energy(state)
            if energy < best_energy:
                best_energy, best = energy, list(state)
        assert best is not None
        return {"energy": float(best_energy), "solution": best,
                "num_states_enumerated": total}

    def coefficient_resolution_audit(self, ratio: float = 200.0) -> dict:
        """Check both effective spread and signed distinct-value separation."""
        if ratio <= 0:
            raise ValueError("ratio must be positive")
        values = sorted({float(v) for v in self.terms.values() if abs(v) > 0})
        if not values:
            spread, max_abs, min_sep, required = 1.0, 0.0, None, 0.0
        else:
            max_abs = max(abs(v) for v in values)
            min_abs = min(abs(v) for v in values)
            spread = max_abs / min_abs
            min_sep = min((b - a for a, b in zip(values, values[1:])), default=None)
            required = max_abs / ratio
        spread_ok = spread <= ratio + 1e-12
        separation_ok = min_sep is None or min_sep + 1e-12 >= required
        passed = spread_ok and separation_ok
        return {
            "resolution_ratio": float(ratio),
            "coefficient_spread": float(spread),
            "max_abs_coefficient": float(max_abs),
            "required_min_pairwise_distinct_separation": float(required),
            "min_pairwise_distinct_separation": (float(min_sep)
                                                  if min_sep is not None else None),
            "spread_within_resolution": spread_ok,
            "pairwise_distinct_separation_resolved": separation_ok,
            "pass": passed,
        }

    def to_qci_polynomial_file(self, file_name: str) -> dict:
        """Create the QCi polynomial-file object; constants stay local."""
        rank = max(1, min(5, self.max_degree))
        data = []
        for key, coefficient in sorted(self.terms.items(), key=lambda item: (len(item[0]), item[0])):
            if len(key) > 5:
                raise ValueError("Dirac-3 supports polynomial degree at most five")
            indices = [0] * (rank - len(key)) + [i + 1 for i in key]
            data.append({"val": float(coefficient), "idx": indices})
        return {
            "file_name": file_name,
            "file_config": {"polynomial": {
                "min_degree": 1,
                "max_degree": rank,
                "num_variables": self.nvars,
                "data": data,
            }},
            "constant_offset_local_only": float(self.constant),
            "coefficient_dynamic_range_local_only":
                float(self.coefficient_resolution_audit()["coefficient_spread"]),
            "coefficient_resolution_audit_local_only":
                self.coefficient_resolution_audit(),
        }
