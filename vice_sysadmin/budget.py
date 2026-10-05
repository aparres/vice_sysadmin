"""Monotonic task deadlines and conservative pre-call resource reservations."""

from dataclasses import dataclass
from decimal import Decimal

from .interfaces import Clock
from .schemas import Config, LLMResponse, ModelSettings, Prices


class LimitReached(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Reservation:
    input_tokens: int
    output_tokens: int
    cost: Decimal
    prices: Prices


def price(prices: Prices, inputs: int, outputs: int) -> Decimal:
    return (Decimal(str(prices.input_per_million)) * inputs
            + Decimal(str(prices.output_per_million)) * outputs) / Decimal(1_000_000)


class Budget:
    def __init__(self, config: Config, clock: Clock):
        self.config, self.clock = config, clock
        self.started = clock.monotonic()
        self.deadline = self.started + config.limits.total_time_seconds
        self.tokens = 0
        self.cost = Decimal(0)

    def check_time(self) -> None:
        if self.clock.monotonic() >= self.deadline:
            raise LimitReached("time_limit")

    def check(self) -> None:
        self.check_time()
        if self.tokens >= self.config.limits.max_tokens:
            raise LimitReached("token_limit")
        if self.cost >= Decimal(str(self.config.limits.cost_cap)):
            raise LimitReached("cost_limit")

    def timeout(self, requested: float) -> float:
        self.check_time()
        return min(requested, self.deadline - self.clock.monotonic())

    def reserve(self, prompt: str, settings: ModelSettings) -> Reservation:
        self.check()
        prices = self.config.pricing.get(settings.model)
        if prices is None:
            raise LimitReached("unpriced_model")
        # Contract for future clients: no hidden prompts beyond this overhead;
        # max_tokens must bound all generated/billed output, including reasoning.
        inputs = len(prompt.encode("utf-8")) + settings.input_token_overhead
        outputs = settings.max_tokens
        cost = price(prices, inputs, outputs)
        if self.tokens + inputs + outputs > self.config.limits.max_tokens:
            raise LimitReached("token_limit")
        if self.cost + cost > Decimal(str(self.config.limits.cost_cap)):
            raise LimitReached("cost_limit")
        return Reservation(inputs, outputs, cost, prices)

    def charge(self, reservation: Reservation, response: LLMResponse | None) -> tuple[int, float]:
        """Missing metadata/errors consume the reservation, not a free attempt."""
        inputs = (response.input_tokens if response is not None else None)
        outputs = (response.output_tokens if response is not None else None)
        inputs = reservation.input_tokens if inputs is None else inputs
        outputs = reservation.output_tokens if outputs is None else outputs
        actual = price(reservation.prices, inputs, outputs)
        if response is None or response.estimated_cost is None:
            cost = max(reservation.cost, actual)
        else:
            cost = max(actual, Decimal(str(response.estimated_cost)))
        self.tokens += inputs + outputs
        self.cost += cost
        return inputs + outputs, float(cost)
