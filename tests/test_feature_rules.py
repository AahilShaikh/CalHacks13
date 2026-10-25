"""Feature code checks: what the parametric model accepts from the agent."""
import pytest

from aida.model import ModelError, _empty_state, check_feature_code, generate_script


def test_helper_functions_may_return_but_the_feature_may_not():
    code = 'def profile(r):\n    return Circle(r)\npart = extrude(profile(P.r), P.h)'
    assert check_feature_code(code, first=True)[1] == ['h', 'r']
    with pytest.raises(ModelError, match='return'):
        check_feature_code('part = Box(P.a, P.a, P.a)\nreturn part', first=True)


def test_imports_and_magic_numbers_are_still_rejected():
    with pytest.raises(ModelError, match='import'):
        check_feature_code('import math\npart = Box(P.a, P.a, P.a)', first=True)
    with pytest.raises(ModelError, match=r'hard-codes numbers \(3\)'):
        check_feature_code('part = Box(3 * P.a, P.a, P.a)', first=True)


def test_generated_scripts_import_math():
    script, _ = generate_script(_empty_state())
    assert 'import math' in script.splitlines()[:3]
