"""SQL injection smoke test — verify all refactored DB calls block injection."""
import datetime

from src.models.repository import DataRepository


def test_get_daily_data_blocks_injection():
    r = DataRepository()
    malicious = "000001' OR '1'='1"
    df = r.get_daily_data(malicious, datetime.date(2024, 1, 1), datetime.date(2024, 6, 1))
    assert df.empty, f"Injection succeeded! Got {len(df)} rows"
    print("[OK] get_daily_data blocks injection")


def test_get_latest_finance_for_codes_blocks_injection():
    r = DataRepository()
    malicious = ["000001", "' OR '1'='1"]
    df = r.get_latest_finance_for_codes(malicious)
    rows_for_injection = df[df["code"].astype(str).str.contains("OR", na=False)]
    assert rows_for_injection.empty, "IN clause injection succeeded!"
    print("[OK] get_latest_finance_for_codes blocks IN injection")


def test_get_fund_flow_for_code_blocks_injection():
    r = DataRepository()
    malicious = "000001' UNION SELECT 1,2,3,4,5,6 --"
    df = r.get_fund_flow_for_code(malicious, "2024-06-01")
    assert df.empty, "UNION injection succeeded!"
    print("[OK] get_fund_flow_for_code blocks UNION injection")


if __name__ == "__main__":
    test_get_daily_data_blocks_injection()
    test_get_latest_finance_for_codes_blocks_injection()
    test_get_fund_flow_for_code_blocks_injection()
    print("\nAll injection tests passed.")