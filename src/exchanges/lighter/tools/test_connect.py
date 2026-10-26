"""Check the Lighter setup step by step and say what to fix.

    python -m src.exchanges.lighter.tools.test_connect
    python -m src.exchanges.lighter.tools.test_connect --public-only

Setup for trading:
1. LIGHTER_ACCOUNT_INDEX: your account index (or LIGHTER_WALLET_ADDRESS to look it up).
2. Create an API key for that account (Lighter app, or the lighter-python SDK example
   examples/system_setup.py) and set LIGHTER_API_KEY_INDEX (4-254) + LIGHTER_API_PRIVATE_KEY.
3. Download the signer library: python -m src.exchanges.lighter.signer --download
"""

import argparse

from src.exchanges.lighter.exchange import test_connection

HINTS = {
    'Tài khoản (LIGHTER_ACCOUNT_INDEX)': "Đặt LIGHTER_ACCOUNT_INDEX (hoặc LIGHTER_WALLET_ADDRESS) trong .env",
    'Thư viện ký (signer)': "Chạy: python -m src.exchanges.lighter.signer --download",
    'Kiểm tra API key': "Key trong .env không khớp key đã đăng ký cho LIGHTER_API_KEY_INDEX — tạo lại API key",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the Lighter connection")
    parser.add_argument("--public-only", action="store_true", help="skip API key checks")
    parser.add_argument("--symbol", default="BTC")
    args = parser.parse_args()

    steps = test_connection(check_private=not args.public_only, symbol=args.symbol)
    for item in steps:
        print(f"{'✅' if item['ok'] else '❌'} {item['step']}: {item['detail']}")
        if not item['ok'] and item['step'] in HINTS:
            print(f"   → {HINTS[item['step']]}")
    if all(item['ok'] for item in steps) and not args.public_only:
        print("\nSẵn sàng giao dịch trên Lighter.")


if __name__ == "__main__":
    main()
