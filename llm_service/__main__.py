"""Quick manual test from the repo root:

    python -m llm_service chat "hello"
    python -m llm_service chat "what is this?" -i chart.png       # repeat -i for more images
    python -m llm_service models --free --vision
"""

from __future__ import annotations

import argparse
import json
import sys

from .client import LLMClient
from .schema import LLMError


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m llm_service")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("chat", help="send one message")
    c.add_argument("prompt", nargs="?", default="", help="text; use - to read stdin")
    c.add_argument("-i", "--image", action="append", default=[], help="image path, URL or base64 (repeatable)")
    c.add_argument("-s", "--system")
    c.add_argument("-m", "--model")
    c.add_argument("-p", "--provider")
    c.add_argument("-t", "--temperature", type=float)
    c.add_argument("--max-tokens", type=int)
    c.add_argument("--json", action="store_true", help="print the full result as JSON")

    m = sub.add_parser("models", help="list models")
    m.add_argument("-p", "--provider")
    m.add_argument("--free", action="store_true")
    m.add_argument("--vision", action="store_true", help="only models that accept images")

    args = p.parse_args(argv)
    try:
        with LLMClient() as client:
            if args.cmd == "chat":
                prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
                r = client.chat(
                    prompt,
                    images=args.image,
                    system=args.system,
                    model=args.model,
                    provider=args.provider,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                )
                if args.json:
                    print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
                else:
                    print(r.text)
                    print(f"\n[{r.provider} | {r.model} | {r.usage.get('total_tokens', '?')} tokens]", file=sys.stderr)
            else:
                items = client.list_models(args.provider, free=args.free, vision=args.vision)
                for it in items:
                    mods = ",".join((it.get("architecture") or {}).get("input_modalities") or [])
                    print(f"{it.get('id'):<60} ctx={it.get('context_length')}  in={mods}")
                print(f"\n{len(items)} models", file=sys.stderr)
    except LLMError as e:
        print(f"error: {e.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
