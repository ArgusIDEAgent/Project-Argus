"""Isolated fixture kept alive for VS Code integration; stdin EOF cleans it up."""

import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_phase6_execution as phase6
from query_system.evidence_aggregation import EvidenceAggregator
from query_system.presentation_contracts import PreviewInput


def main():
    owner = phase6.ExecutionTests(); owner.setUp()
    try:
        with owner.session() as executor:
            query = owner.typed_requirements(executor, ["SYMBOL_LOCATION"] * 2, [[0], [1]])
            package = EvidenceAggregator.from_executor(executor).aggregate(asyncio.run(executor.execute(executor.plan(query))))
        print(json.dumps({"rootPath": str(owner.fixture.root), "dataDir": str(owner.fixture.data),
            "packet": PreviewInput(package=package).model_dump(mode="json")}), flush=True)
        sys.stdin.buffer.read(1)
    finally:
        owner.doCleanups()


if __name__ == "__main__":
    main()
