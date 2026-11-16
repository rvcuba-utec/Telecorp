"""RF-AWS-04. El 0 se evalúa primero."""
NORMAL, CAIDA, INHABILITADA, SATURADA = "NORMAL", "CAIDA", "INHABILITADA", "SATURADA"
STATES = (NORMAL, CAIDA, INHABILITADA, SATURADA)
ANOMALOUS = (CAIDA, INHABILITADA, SATURADA)


def classify(x: float, down: float = 60, saturated: float = 129) -> str:
    """x en GB/min: 0 -> INHABILITADA; <60 -> CAIDA; 60..129 -> NORMAL; >129 -> SATURADA."""
    if x == 0:
        return INHABILITADA
    if x < down:
        return CAIDA
    if x <= saturated:
        return NORMAL
    return SATURADA
