import stock_forensics


def test_package_importable() -> None:
    assert stock_forensics.__version__ == "0.1.0"
