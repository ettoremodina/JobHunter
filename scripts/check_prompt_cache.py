"""Verifica se la cache di contesto del provider aggancia, spendendo due chiamate.

La cache **implicita** è dichiarata «not guaranteed» dal provider, e infatti non ha mai agganciato:
`prompt_tokens_details.cached_tokens = 0` su 393.699 token di prompt in 154 chiamate misurate.
Quella **esplicita** è deterministica ma va chiesta: `cache_control: {"type": "ephemeral"}` sul
blocco da riusare, e il prefisso deve superare i 1.024 token. Entrambe le condizioni sono ora
soddisfatte — `config/remote_llm.json → cache.explicit` e il messaggio di sistema che porta
prompt, profilo ed etichette — e questo script serve a vederlo confermato dal provider.

La seconda chiamata identica deve riportare `cached_tokens > 0`.

Costa **due chiamate API vere** (~4k token in tutto) e non salva nulla in archivio.

    python scripts/check_prompt_cache.py --spend
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobhunter.evaluation import company_batch as cb, remote_llm as llm  # noqa: E402
from jobhunter.evaluation.selection import verdicts  # noqa: E402
from jobhunter.workspace import Archive, ROOT, settings  # noqa: E402


def check(archive, calls=2):
    """Send the same request twice and report what the provider says about its own cache."""
    cfg = json.loads((ROOT/'config/remote_llm.json').read_text())
    prompt = (ROOT/cfg['company_batch']['prompt_path']).read_text(encoding='utf-8')
    counts = Counter()
    job = next((j for j in (cb.prepare(archive, cfg, prompt, cid, state, set(), counts)
                            for cid, state in verdicts(archive).items()) if j and j['judged']), None)
    if job is None:
        raise SystemExit('Nessuna azienda con annunci da giudicare: niente da misurare.')
    key = llm.api_key(cfg)
    payload = job['payload']
    stable = len(prompt) + len(payload.get('candidate_profile', '')) + len(json.dumps(payload.get('field_labels'), ensure_ascii=False))
    mode = 'esplicita' if cfg.get('cache', {}).get('explicit') else 'implicita (non garantita dal provider)'
    print(f"azienda {job['id']} · {len(job['judged'])} annunci da giudicare · cache {mode}")
    print(f"prefisso di sistema: ~{stable // 4} token (prompt + profilo + field_labels), minimo richiesto 1024")
    if stable // 4 < 1024:
        print("ATTENZIONE: sotto la soglia, il provider non metterà nulla in cache.")
    for attempt in range(1, calls + 1):
        _, usage, _ = llm.request({**cfg, 'parameters': {**cfg['parameters'], 'max_tokens': cfg['company_batch']['max_tokens']}},
                                  prompt, payload, key)
        detail = usage.get('prompt_tokens_details') or {}
        cached = detail.get('cached_tokens', 0)
        total = usage.get('prompt_tokens', 0)
        print(f"  chiamata {attempt}: prompt {total} token · cached {cached} ({100 * cached / max(total, 1):.0f}%)")
    print("\ncached > 0 alla seconda chiamata = la cache aggancia: il prefisso stabile viene pagato meno.")
    print("cached = 0 su entrambe = il provider non la applica qui; solo allora conviene valutare")
    print("il batch di piu' aziende per chiamata, con i rischi che comporta.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spend', action='store_true', help='Conferma esplicita: esegue due chiamate a pagamento')
    parser.add_argument('--calls', type=int, default=2)
    arguments = parser.parse_args()
    if not arguments.spend:
        raise SystemExit('Servono due chiamate API reali. Rilancia con --spend per confermare.')
    workspace = Archive(ROOT/settings()['database'])
    try:
        check(workspace, max(2, arguments.calls))
    finally:
        workspace.close()
