import json
import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt

ENV_CONFIG = "AGENTEK_GATEWAY_CONFIG"


class ProviderTuning(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    soft_threshold_percent: float = Field(default=95.0, gt=0, le=100)
    soft_until_tolerance_s: PositiveFloat = 60.0
    five_hour_block_cap_s: PositiveFloat = 30 * 60
    implausible_reset_max_s: PositiveFloat = 8 * 24 * 3600
    implausible_block_s: PositiveFloat = 5 * 60
    no_reset_block_s: PositiveFloat = 5 * 60
    overload_base_s: PositiveFloat = 60.0
    overload_cap_s: PositiveFloat = 10 * 60
    series_threshold: PositiveInt = 3
    series_window_s: PositiveFloat = 120.0
    broken_after: PositiveInt = 5
    auth_refresh_cap_s: PositiveFloat = 10 * 60
    half_open_probe_interval_s: PositiveFloat = 30.0
    broken_probe_interval_s: PositiveFloat = 30 * 60
    concurrency_limit: PositiveInt | None = None
    slot_ttl_s: PositiveFloat = 15 * 60
    unsupported_model_ttl_s: PositiveFloat = 24 * 3600
    sticky_ttl_s: PositiveFloat = 24 * 3600
    no_capacity_retry_after_s: PositiveInt = 10


class GatewayConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    defaults: ProviderTuning = ProviderTuning()
    providers: Mapping[str, Mapping[str, object]] = Field(default_factory=dict)

    def tuning_for(self, provider: str) -> ProviderTuning:
        overrides = self.providers.get(provider)
        if not overrides:
            return self.defaults
        return ProviderTuning.model_validate(
            {**self.defaults.model_dump(), **overrides}
        )


def load_config(raw: str | None) -> GatewayConfig:
    if not raw:
        return GatewayConfig()
    config = GatewayConfig.model_validate(json.loads(raw))
    for provider in config.providers:
        config.tuning_for(provider)
    return config


def config_from_env(environ: Mapping[str, str] | None = None) -> GatewayConfig:
    source = os.environ if environ is None else environ
    return load_config(source.get(ENV_CONFIG))
