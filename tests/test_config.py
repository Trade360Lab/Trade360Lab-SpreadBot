from pathlib import Path

from settings import load_settings


def test_config_loading_respects_overlay_and_safe_defaults(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    overlay = tmp_path / "test.yaml"
    overlay.write_text(
        """
strategy:
  order_size: 0.002
risk:
  max_inventory: 0.05
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(str(overlay))

    assert settings.app.dry_run is True
    assert settings.real_trading_enabled is False
    assert settings.strategy.order_size == 0.002
    assert settings.risk.max_inventory == 0.05
    assert (tmp_path / "data" / "raw").exists()
