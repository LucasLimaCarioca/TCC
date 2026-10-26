"""Erros estáveis sem registros históricos ou detalhes de exceções."""


class ExperimentError(ValueError):
    def __init__(self, code):
        self.code = code
        messages = {
            "INVALID_INPUT": "Informe manifestos privados ou selecione a validação somente funcional.",
            "INVALID_PATH": "A validação exige caminhos privados ignorados e fora do índice Git.",
            "INVALID_ARTIFACT": "Não foi possível validar os artefatos de previsão informados.",
            "REPORT_FAILED": "Não foi possível gravar as evidências privadas da validação.",
            "EXPERIMENT_FAILED": "A validação funcional não concluiu todos os critérios.",
        }
        super().__init__(messages[code])
