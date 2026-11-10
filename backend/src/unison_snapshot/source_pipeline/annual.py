"""Annual version selection and field projection shared by source adapters."""


class ReportSelection:
    """Keep each identity's highest source-provided rank, with explicit tie policy."""
    def __init__(self, *, keep_ties):
        self.keep_ties = keep_ties
        self.ranks = {}
        self.groups = {}

    def add(self, identity, rank, report):
        if identity not in self.ranks or rank > self.ranks[identity]:
            self.ranks[identity] = rank
            self.groups[identity] = [report]
        elif self.keep_ties and rank == self.ranks[identity]:
            self.groups[identity].append(report)


def project_row(row, columns, **fields):
    return {**{target: row[source] for target, source in columns.items()}, **fields}
