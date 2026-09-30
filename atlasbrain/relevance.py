"""Portão de relevância: decide se uma informação merece virar nota permanente.

Em vez de confiar só no "achismo" do LLM, cada candidato recebe uma nota de 0 a 1 formada por critérios
nomeados e verificáveis. A nota fica gravada no frontmatter (`relevancia`) e toda recusa diz o porquê.

    específico   cita coisas concretas: números, arquivos, nomes, identificadores de código
    durável      vale daqui a meses; não é passo de execução ("rodei", "vou testar", "deu erro")
    decisivo     (decisão) algo foi de fato escolhido, não só cogitado
    não óbvio    (aprendizado) traz causa, medida ou pegadinha, não o que a documentação já diz
    justificado  explica o porquê

Quando a captura automática manda também a opinião do LLM (importância e durabilidade de 1 a 5), ela
entra como um sinal a mais, com peso menor que os critérios verificáveis.
"""

import re
from dataclasses import dataclass, field

WEIGHTS = {
    "decisao": {"especifico": 0.25, "duravel": 0.25, "decisivo": 0.3, "justificado": 0.2},
    "aprendizado": {"especifico": 0.3, "duravel": 0.25, "nao_obvio": 0.3, "justificado": 0.15},
}
MINIMUM_SCORES = {"manual": 0.45, "automatico": 0.55}  # calibrado: ruins ≤ 0.41, bons ≥ 0.58  # o agente pediu explicitamente × captura por conta própria

_NUM = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:%|ms|s|min|h|mb|gb|kb|x|r\$)?", re.I)
_PATH = re.compile(r"[\w.-]+/[\w./-]+|\b[\w-]+\.(?:py|tsx?|jsx?|go|rs|java|rb|php|sql|ya?ml|toml|json|md|css)\b")
_IDENT = re.compile(r"`[^`]+`|\b[a-z]+[A-Z]\w*\b|\b\w+_\w+\b|\b[A-Z]{2,}\w*\b")
_PROPER_NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]{2,}")
_EPHEMERAL = re.compile(
    r"\b(rodei|rodando|vou (testar|rodar|olhar|ver|tentar)|deu erro|acabei de|estou (fazendo|rodando|testando)|"
    r"fiz o commit|commitei|agora vou|próximo passo é|abri o arquivo|li o arquivo|instalei|reiniciei|"
    r"ajustei a linha|corrigi o typo|typo|screenshot|print da tela)\b", re.I)
_DECISIVE = re.compile(
    r"\b(decid\w*|escolh\w*|optamos|definimos|definid[oa]s?|passa(?:m)? a|opt\w+ por|vamos (usar|manter|adotar|seguir|trocar|priorizar|parar)|"
    r"não vamos|fica(?:rá)? definido|padrão (será|é)|a partir de agora|adot\w+|padroniz\w+|prioriz\w+|"
    r"em vez de|ao invés de|substitu\w+|trocar (?:o|a|os|as)? ?\w+ por|será (usado|feito)|usar \w+ (para|no|na|em))\b", re.I)
_TENTATIVE = re.compile(r"\b(talvez|quem sabe|poderíamos|podemos pensar|seria bom|avaliar se|considerar)\b", re.I)
_NON_OBVIOUS = re.compile(
    r"\b(porque|pois|causa|na verdade|pegadinha|cuidado|não funciona|quebra|falha|só funciona|medido|"
    r"medimos|descobri\w*|diferente do que|ao contrário|inesperad\w+|silenciosamente|limite de|exige|precisa de)\b", re.I)
_REASON = re.compile(r"\b(porque|pois|para que|já que|motivo|por causa|evita|garante|senão|caso contrário)\b", re.I)


@dataclass
class Evaluation:
    score: float
    criteria: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)
    accepted: bool = True

    def summary(self) -> str:
        criteria_text = ", ".join(f"{k} {v:.1f}" for k, v in self.criteria.items())
        return f"relevância {self.score:.2f} ({criteria_text})" + (f" — {'; '.join(self.reasons)}" if self.reasons else "")


def evaluate(kind: str, title: str, text: str, reason: str = "", mode: str = "manual",
            llm: dict | None = None) -> Evaluation:
    combined_text = f"{title}. {text} {reason}"
    if len(f"{title} {text}".strip()) < 20 or len(text.strip()) < 8:
        return Evaluation(0.0, {}, ["texto curto demais para virar nota"], False)

    concrete_signals = len(_NUM.findall(combined_text)) + 2 * len(_PATH.findall(combined_text)) + len(_IDENT.findall(combined_text)) \
        + 0.5 * len(_PROPER_NAME.findall(combined_text))
    c = {"especifico": min(1.0, concrete_signals / 3)}
    ephemeral_count = len(_EPHEMERAL.findall(combined_text))
    c["duravel"] = max(0.0, 1 - 0.4 * ephemeral_count)
    if kind == "decisao":
        d = 1.0 if _DECISIVE.search(combined_text) else 0.35
        if _TENTATIVE.search(combined_text) and not _DECISIVE.search(combined_text):
            d = 0.1
        c["decisivo"] = d
    else:
        c["nao_obvio"] = min(1.0, 0.35 + 0.35 * len(_NON_OBVIOUS.findall(combined_text)) + (0.2 if _NUM.search(combined_text) else 0))
    c["justificado"] = 1.0 if (reason.strip() or _REASON.search(combined_text)) else 0.3

    weights = WEIGHTS[kind]
    score = sum(c[k] * w for k, w in weights.items())
    if llm:  # opinião do LLM (1 a 5): sinal auxiliar, 25% do peso
        vals = [v for v in (llm.get("importancia"), llm.get("durabilidade")) if isinstance(v, (int, float))]
        if vals:
            score = 0.75 * score + 0.25 * ((sum(vals) / len(vals)) - 1) / 4

    reasons = []
    if c["especifico"] < 0.34:
        reasons.append("vago: sem nada concreto (número, arquivo, nome, identificador)")
    if c["duravel"] < 0.6:
        reasons.append("parece passo de execução, não algo que vale daqui a meses")
    if c.get("decisivo", 1) < 0.4:
        reasons.append("não há uma escolha fechada (só cogitada ou descrita)")
    if c.get("nao_obvio", 1) < 0.4:
        reasons.append("não traz causa, medida ou pegadinha")
    if c["justificado"] < 0.5:
        reasons.append("falta o porquê")
    accepted = score >= MINIMUM_SCORES[mode]
    return Evaluation(round(score, 3), {k: round(v, 2) for k, v in c.items()}, reasons, accepted)
