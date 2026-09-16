"""Company business statements from saved jobs, separate from role requirements."""

import json
import re


def job_facts(archive, cid, company_name):
    """Collect explicit employer statements across all roles with source provenance."""
    from jobhunter.evaluation.enrichment import lines
    subject = r'(?:we|our company|the company|noi|la nostra azienda|' + re.escape(company_name) + r')'
    # «Dice cosa fa» e «dice cosa e'» sono due frasi diverse: la prima vale da sola, la seconda
    # ha bisogno di un sostantivo d'impresa, altrimenti passa anche «We are excited to announce».
    doing = r'(?:build|develop|manufactur|produc|provid|design|operat|speciali[sz]|sviluppiam|offriam)\w*'
    activity = re.compile(r'^' + subject + r'\s+(?:(?:is|are|siamo)\s+)?' + doing + r'\b', re.I)
    hiring = re.compile(r'\b(?:looking for|seeking|hiring|recruiting|you will|your role|your responsibilities|cerchiamo|ricerchiamo)\b', re.I)
    values = re.compile(r'\b(?:committed to|committed in|our values|community of|all united|equal opportunity|certified b corporation|employee benefits|for our employees|for our staff)\b', re.I)
    identity_statement = re.compile(r'^' + subject + r'\s+(?:is|are|siamo)\b', re.I)
    # La `s?` finale copre providers, banks, platforms, leaders, markets: un plurale costava una frase intera.
    business = re.compile(r'\b(?:company|companies|business(?:es)?|provider|manufacturer|manufacturing|bank|agency|agencies|platform|leader|leading|market|kitchen|azienda|aziende|società|produzione)s?\b', re.I)
    result, seen = [], set()
    for row in archive.db.execute('SELECT id,data FROM opportunities WHERE company_id=? ORDER BY id', (cid,)):
        job = json.loads(row['data'])
        for paragraph in lines(job.get('description', '')):
            for sentence in re.split(r'(?<=[.!?])\s+', paragraph):
                text = sentence.strip()
                if (text and text not in seen and not hiring.search(text) and not values.search(text)
                        and (activity.search(text) or identity_statement.search(text) and business.search(text))):
                    seen.add(text)
                    result.append({'text': text, 'source_url': job['source_url'], 'opportunity_id': row['id']})
    return result
