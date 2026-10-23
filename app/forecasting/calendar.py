"""Calendário diário de operação em Manaus, com regras locais verificadas.

O calendário trata dias com alguma possibilidade de operação como abertos.
Assim, o feriado de Cinzas até 12h não exclui a tarde da série diária. Pontos
facultativos do serviço público não fecham automaticamente uma fábrica privada.
Nenhuma consulta de rede acontece durante treinamento ou previsão.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from app.forecasting.errors import ForecastError


CALENDAR_VERSION = "manaus-2021-2027-v1"
SUPPORTED_YEARS = frozenset(range(2021, 2028))

# Fontes públicas oficiais; nunca incluem dados ou informações da fábrica.
_SOURCES = (
    ("national_fixed", "Lei federal 662/1949, redação da Lei 10.607/2002",
     "https://www.planalto.gov.br/ccivil_03/leis/l0662.htm"),
    ("aparecida", "Lei federal 6.802/1980",
     "https://www.planalto.gov.br/ccivil_03/leis/l6802.htm"),
    ("national_november20", "Lei federal 14.759/2023; publicada em 22/12/2023",
     "https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2023/lei/l14759.htm"),
    ("amazonas_september5", "Lei promulgada AM 25/1977",
     "https://sapl.al.am.leg.br/norma/8919"),
    ("amazonas_november20", "Lei promulgada AM 84/2010",
     "https://sapl.al.am.leg.br/media/sapl/public/normajuridica/2010/2873/2873_texto_integral.pdf"),
    ("manaus_carnival", "Lei municipal 448/1998; Cinzas somente até 12h",
     "https://sapl.cmm.am.gov.br/media/sapl/public/normajuridica/1998/3871/lei_n_448_de_11_nov_1998.pdf"),
    ("manaus_december8", "Lei municipal 496/1999",
     "https://sapl.cmm.am.gov.br/norma/3932"),
    ("manaus_civic", "Lei Orgânica de Manaus, art. 437, I e II",
     "https://sapl.cmm.am.gov.br/media/sapl/public/normajuridica/2005/6591/loman_atualizada_ate_emenda_120_19a_leg.pdf"),
    ("manaus_calendar_2026", "DOM 6251, 11/02/2026; leis 970/2006 e 1.001/2006",
     "https://www.manaus.am.gov.br/wp-content/uploads/2026/02/Prefeitura-de-Manaus-publica-calendario-de-feriados-e-pontos-facultativos-municipais-no-Diario-Oficial.pdf"),
    ("suspension_2020", "Lei municipal 2.660/2020, revogada em 30/03/2021",
     "https://sapl.cmm.am.gov.br/media/sapl/public/normajuridica/2020/1984/lei_n_2660_de_21_ago_2020_.pdf"),
    ("carnival_2021", "CREA-AM, deliberação 19/2021: suspensão do Carnaval de 2021",
     "https://crea-am.org.br/creaam_site/wp-content/uploads/DD-19-2021-APROVACAO-DO-CALENDARIO-DE-FERIADOS-MUNICIPAIS-E-NACIONAIS-DE-2021.pdf"),
    ("ash_wednesday_2021", "DOM 5034, 15/02/2021; Decreto municipal 5.025/2021",
     "https://sapl.cmm.am.gov.br/media/sapl/public/normajuridica/2021/2142/lei_n_2731_de_15_fev_2021.pdf"),
    ("suspension_repeal_2021", "Lei 2.735/2021 revoga 2.660 e autoriza novos atos",
     "https://sapl.cmm.am.gov.br/media/sapl/public/normajuridica/2021/2254/lei_n_2735_de_30_mar_2021.pdf"),
    ("corpus_christi_2021", "Prefeitura confirma Corpus Christi em 03/06/2021",
     "https://www.manaus.am.gov.br/noticia/formacao/calendario-de-aulas-dos-residentes-juridicos-junho/"),
    ("december8_2021", "Prefeitura confirma feriado de 08/12/2021",
     "https://www.manaus.am.gov.br/noticia/imunizacao/prefeitura-tera-52-pontos-de-vacinacao-contra-a-covid-19-entre-6-e-10-12/"),
)


@dataclass(frozen=True)
class _Holiday:
    name: str
    scope: str
    legal_basis: str
    partial_day: bool = False


def _easter(year: int) -> date:
    """Computus gregoriano (Meeus/Jones/Butcher), sem dependência externa."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    value = h + l - 7 * m + 114
    return date(year, value // 31, value % 31 + 1)


class ManausCalendar:
    """Segunda a sexta sem feriados integrais, nos anos explicitamente cobertos.

    ``overrides`` registra exceções operacionais confirmadas: True abre o dia,
    False fecha. Não altera o nome nem a base legal de um feriado. A cobertura
    termina em 2027 e deve ser revisada antes de incluir novos anos.
    """

    def __init__(
        self, years: Iterable[int], *, overrides: Mapping[date, bool] | None = None,
    ):
        try:
            provided = tuple(years)
        except TypeError as exc:
            raise ForecastError("Informe os anos de cobertura do calendário.") from exc
        if not provided or any(type(year) is not int for year in provided):
            raise ForecastError("Os anos do calendário devem ser inteiros e não vazios.")
        self.years = tuple(sorted(set(provided)))
        if not set(self.years) <= SUPPORTED_YEARS:
            raise ForecastError("Calendário verificado somente para os anos de 2021 a 2027.")
        if overrides is not None and not isinstance(overrides, Mapping):
            raise ForecastError("As exceções do calendário devem ser um mapa de datas e booleanos.")
        self._overrides = dict(overrides or {})
        for day, opened in self._overrides.items():
            self._validate_day(day)
            if type(opened) is not bool:
                raise ForecastError("Cada exceção do calendário deve indicar True ou False.")
        self._holidays = {}
        for year in self.years:
            self._add_year(year)

    def _validate_day(self, day: date) -> None:
        # datetime é subclasse de date, mas não é um dia comercial sem fuso.
        if type(day) is not date:
            raise ForecastError("Use uma data comercial sem horário no calendário.")
        if day.year not in self.years:
            raise ForecastError("Data fora da cobertura solicitada do calendário.")

    def _add_year(self, year: int) -> None:
        for month, day, name in (
            (1, 1, "Confraternização Universal"),
            (4, 21, "Tiradentes"),
            (5, 1, "Dia do Trabalho"),
            (9, 7, "Independência do Brasil"),
            (11, 2, "Finados"),
            (11, 15, "Proclamação da República"),
            (12, 25, "Natal"),
        ):
            self._holidays[date(year, month, day)] = _Holiday(name, "national", "national_fixed")
        for month, day, name, scope, basis in (
            (10, 12, "Nossa Senhora Aparecida", "national", "aparecida"),
            (9, 5, "Elevação do Amazonas à categoria de Província", "state_and_municipal", "amazonas_september5;manaus_civic"),
            (10, 24, "Elevação de Manaus à categoria de Cidade", "municipal", "manaus_civic"),
            (12, 8, "Nossa Senhora da Conceição", "municipal", "manaus_december8"),
        ):
            self._holidays[date(year, month, day)] = _Holiday(name, scope, basis)
        # A lei federal foi publicada depois de 20/11/2023. Nos anos anteriores
        # o fechamento em Manaus já decorre da lei estadual de 2010.
        november20_scope = "national" if year >= 2024 else "state"
        november20_basis = "national_november20" if year >= 2024 else "amazonas_november20"
        self._holidays[date(year, 11, 20)] = _Holiday(
            "Dia de Zumbi e da Consciência Negra", november20_scope, november20_basis,
        )
        easter = _easter(year)
        self._holidays[easter - timedelta(days=2)] = _Holiday(
            "Sexta-feira da Paixão", "municipal", "manaus_calendar_2026 (Lei 1.001/2006)",
        )
        self._holidays[easter + timedelta(days=60)] = _Holiday(
            "Corpus Christi", "municipal", "manaus_calendar_2026 (Lei 970/2006)",
        )
        # Os atos de suspensão de 2021 não são repetidos nos anos seguintes.
        if year != 2021:
            self._holidays[easter - timedelta(days=47)] = _Holiday(
                "Terça-feira de Carnaval", "municipal", "manaus_carnival",
            )
            self._holidays[easter - timedelta(days=46)] = _Holiday(
                "Quarta-feira de Cinzas (até 12h)", "municipal", "manaus_carnival", True,
            )

    def is_open(self, day: date) -> bool:
        """True se existe expediente no dia; nunca presume um ano desconhecido."""
        self._validate_day(day)
        if day in self._overrides:
            return self._overrides[day]
        holiday = self._holidays.get(day)
        return day.weekday() < 5 and (holiday is None or holiday.partial_day)

    def holiday_name(self, day: date) -> str | None:
        """Nome do feriado legal (inclui Cinzas parcial), independente de exceção."""
        self._validate_day(day)
        holiday = self._holidays.get(day)
        return holiday.name if holiday else None

    def metadata(self) -> dict:
        """Metadados novos a cada chamada, adequados a JSON e auditoria local."""
        return {
            "calendar_id": "BR-AM-Manaus",
            "version": CALENDAR_VERSION,
            "verified_on": "2026-10-22",
            "years": list(self.years),
            "coverage": {"supported_years": sorted(SUPPORTED_YEARS), "outside_coverage": "error"},
            "rules": {
                "open_weekdays": [0, 1, 2, 3, 4],
                "full_holidays": "closed",
                "partial_holidays": "open_if_any_operation_is_possible",
                "ash_wednesday": "closed_until_12h_only; afternoon_not_automatically_closed",
                "public_optional_days": "not_automatically_closed_for_private_factory",
                "holiday_observation": "actual_date_only; no_automatic_monday_transfer",
                "easter_algorithm": "Gregorian Meeus/Jones/Butcher",
                "november20": "state_2010; national_only_after_publication_2023-12-22",
                "2027": "recurring_laws_only; no_projection_of_public_optional_days",
                "operational_exceptions": "explicit_overrides_take_precedence",
            },
            "legal_exceptions": [
                {"date": "2021-02-16", "rule": "Carnaval suspenso", "source": "carnival_2021"},
                {"date": "2021-02-17", "rule": "Cinzas suspenso", "source": "ash_wednesday_2021"},
                {"date": "2021-03-30", "rule": "Revogação da suspensão geral da Lei 2.660/2020; autorização para novos atos não fecha nem abre dias automaticamente", "source": "suspension_repeal_2021"},
            ],
            "limitations": [
                "Granularidade diária não mede redução de expediente em feriados parciais.",
                "Recessos, fechamentos internos e restrições operacionais da pandemia exigem exceções confirmadas da fábrica.",
                "A cobertura futura usa leis recorrentes verificadas em 22/10/2026; alterações legais posteriores exigem nova versão.",
            ],
            "overrides": [{"date": day.isoformat(), "is_open": opened}
                          for day, opened in sorted(self._overrides.items())],
            "holidays": [{"date": day.isoformat(), "name": holiday.name,
                          "scope": holiday.scope, "legal_basis": holiday.legal_basis,
                          "partial_day": holiday.partial_day}
                         for day, holiday in sorted(self._holidays.items())],
            "sources": [{"id": key, "title": title, "url": url} for key, title, url in _SOURCES],
        }
