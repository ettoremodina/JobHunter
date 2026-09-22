"""Extract explicit language requirements without treating a country as a language constraint.

Qui vive anche il riconoscimento della lingua in cui l'annuncio è scritto, che è un'altra cosa:
un annuncio in tedesco può non chiedere il tedesco, e uno in inglese può chiederlo.
"""

import re
from collections import Counter
from jobhunter.evaluation.enrichment import lines

# Parole funzione: poche, frequentissime e difficili da evitare scrivendo nella propria lingua.
# Bastano per dire in che lingua è scritto un annuncio; non servono a tradurlo.
STOPWORDS = {
    'it': 'il lo la i gli le di che per con del della nel alla una un sono anche come nostro ruolo azienda esperienza offriamo cerchiamo tuo tua nella dei alle',
    'en': 'the and for with you our we are will your that this from have their team role work experience about',
    'de': 'und der die das mit für sie wir bei von den dem ein eine ist auch als sich oder unsere ihre werden',
    'fr': 'et les des une dans pour vous nous avec est sur par votre au aux sont ou plus notre chez qui',
    'es': 'los las una del para con que por como nuestro nuestra sus está son también su experiencia trabajo',
    'pt': 'os as uma dos das para com que por como nosso nossa você sua está são também trabalho experiência',
    'nl': 'het een van en voor met je onze wij zijn bij ook als naar deze door aan uit werk ervaring',
    'sv': 'och att för med som den det vi du har till på inte kommer arbete erfarenhet vår våra',
    'da': 'og at for med som den det vi du har til på ikke vil arbejde erfaring vores dine',
    'no': 'og at for med som den det vi du har til på ikke vil arbeid erfaring vår våre deg',
    'pl': 'nie oraz dla przez jest są się który która które będzie pracy doświadczenie nasze twoje jako',
    'fi': 'ja on että sekä kanssa hakemus työn osaamista sinulla meillä tarjoamme tehtävään kokemusta',
    'cs': 'nebo pro jsou které jako naše práce zkušenosti budete vám také již velmi',
    'ro': 'și pentru care este sunt cu din nostru noastre experiență echipa vei vom',
}
NAMES = {'it': 'italiano', 'en': 'inglese', 'de': 'tedesco', 'fr': 'francese', 'es': 'spagnolo', 'pt': 'portoghese',
         'nl': 'olandese', 'sv': 'svedese', 'da': 'danese', 'no': 'norvegese', 'pl': 'polacco', 'fi': 'finlandese',
         'cs': 'ceco', 'ro': 'rumeno'}
KNOWN = ('it', 'en')
_PROFILES = {code: set(words.split()) for code, words in STOPWORDS.items()}

# These markers intentionally describe obligation, not mere co-occurrence with a language name.
# The clause must still contain one of the configured language patterns before either expression
# is considered. Optional wording wins when both kinds of marker occur in the same clause.
OPTIONAL_LANGUAGE = re.compile(
    r'not (?:required|necessary|mandatory|essential)|no .{0,20}required|preferred|a plus|'
    r'nice.to.have|optional|advantage|\basset\b|\btroef\b|\bmerit(?:erande)?\b|\bfördel\b|'
    r'non (?:sono |è )?(?:richiest|necessari|obbligator)|preferenzial|facoltativ|'
    r'von vorteil|wünschenswert', re.I)
MANDATORY_LANGUAGE = re.compile(
    r'required|must|essential|mandatory|fluen\w*|proficien\w*|native|business.level|'
    r'\b[bBcC][12]\b|richiest\w*|obbligator\w*|padronanza|ottima conoscenza|'
    r'\b(?:good|strong|excellent|professional)\b.{0,35}\b(?:written|spoken|knowledge|command)\b|'
    r'\b(?:sehr\s+)?gute\w*\b.{0,40}\b(?:kenntnisse|deutsch|englisch)|'
    r'\bkenntnisse\b.{0,20}\bwort und schrift\b|'
    r'\b(?:god|goda)\s+(?:kunskap|kunskaper)|\bspråkkunskaper\b|\bflytande\b|'
    r'\b(?:muntlig|skriftlig)\w*\b|\btal och skrift\b|\bfremstillingsevne\b|'
    r'\bvloeiend\b|\bbeheersing\b|verhandlungssicher|zwingend|courant|maîtrise', re.I)


def detect_language(text):
    """In che lingua è scritto un annuncio, contando le parole funzione che nessuno riesce a evitare.

    Serve a segnalare gli annunci in una lingua che l'utente non legge, anche quando quella lingua
    non è fra i requisiti. Va letto sul testo originale: la sintesi remota è già tradotta.
    Sotto le venti parole, o senza un vincitore netto, la risposta è «non determinata»: meglio
    dichiarare di non sapere che affibbiare una lingua a caso.
    """
    words = re.findall(r"[^\W\d_]+", (text or '').casefold(), re.UNICODE)
    if len(words) < 20:
        return {'code': '', 'name': 'non determinata', 'confidence': 0.0, 'known': True}
    counts = Counter({code: sum(word in profile for word in words) for code, profile in _PROFILES.items()})
    (code, hits), (_, second) = counts.most_common(2)
    total = sum(counts.values())
    confidence = round(hits / total, 2) if total else 0.0
    if hits < 5 or hits == second or confidence < 0.3:
        return {'code': '', 'name': 'non determinata', 'confidence': confidence, 'known': True}
    return {'code': code, 'name': NAMES[code], 'confidence': confidence, 'known': code in KNOWN}


def language_requirements(text, config):
    """Keep required lists, permitted alternatives and optional or waived language evidence separate."""
    required, alternatives, evidence, optional = set(), [], [], set()
    allowed = set(config.get('allowed_languages', ['Italian', 'English']))
    patterns = config.get('language_patterns', {})
    text = '\n'.join(lines(text))
    if patterns:
        # A comma between bare language names belongs to the list. Other commas
        # still separate clauses such as "English required, German optional".
        names = '|'.join('(?:' + pattern + ')' for pattern in patterns.values())
        text = re.sub(r'(' + names + r')\s*,\s*(?=(?:' + names + r'))',
                      lambda match: match[0].replace(',', '\0'), text, flags=re.I)
    for clause in re.split(r'(?<=[.!?;])\s+|\n|,|\bbut\b|\bmentre\b', text):
        clause = clause.replace('\0', ',')  # Keep source punctuation in evidence quotes.
        found = {name for name, pattern in patterns.items() if re.search(pattern, clause, re.I)}
        if not found:
            continue
        if OPTIONAL_LANGUAGE.search(clause):
            optional.update(found)
            continue
        mandatory = MANDATORY_LANGUAGE.search(clause)
        if not mandatory:
            continue
        if re.search(r'\b(clients|customers|companies|market|office)\b', clause, re.I) and not re.search(r'language|speak|fluen|proficien|native|conoscenza|padronanza', clause, re.I):
            continue
        evidence.append(clause.strip())
        if re.search(r'\b(or|oppure|o|oder|ou)\b', clause, re.I) and not re.search(r'\b(and|e|und|et)\b', clause, re.I):
            alternatives.append(sorted(found))
        else:
            required.update(found)
    unsupported = required - allowed
    for group in alternatives:
        if not allowed.intersection(group):
            unsupported.update(group)
    return {'required': sorted(required), 'alternatives': alternatives, 'optional': sorted(optional),
            'unsupported': sorted(unsupported), 'evidence': evidence,
            'status': 'incompatible' if unsupported else 'compatible' if evidence else 'not_stated'}
