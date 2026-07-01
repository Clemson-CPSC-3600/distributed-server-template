"""
Example test file showing how to use bundle markers for grading.
Each bundle is pass/fail; pass all of a bundle's tests to clear it.
This file can be removed when actual tests are added.
"""
import pytest


@pytest.mark.bundle(1)
def test_bundle1_basic_functionality():
    """Example test for Bundle 1 (Core functionality)."""
    # This would test basic, essential features
    assert True, "Replace with actual test"


@pytest.mark.bundle(2)
def test_bundle2_edge_cases():
    """Example test for Bundle 2 (Intermediate functionality)."""
    # This would test edge cases and error handling
    assert True, "Replace with actual test"


@pytest.mark.bundle(3)
def test_bundle3_advanced_features():
    """Example test for Bundle 3 (Advanced functionality)."""
    # This would test complex scenarios and performance
    assert True, "Replace with actual test"