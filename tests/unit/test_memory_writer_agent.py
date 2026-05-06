import pytest
from google.adk.agents import BaseAgent
from agents.memory.writer import MemoryWriter


def test_memory_writer_is_base_agent():
    assert issubclass(MemoryWriter, BaseAgent)


def test_memory_writer_has_name():
    mw = MemoryWriter()
    assert mw.name == "MemoryWriter"
