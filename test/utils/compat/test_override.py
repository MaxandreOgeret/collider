# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 MOG Robotics OÜ.

import sys
import types

from unittest.mock import patch


def test_override_decorator():
    """Test that the override decorator doesn't break the function it decorates."""
    from collider.utils.compat.override import override

    @override
    def my_function(x):
        return x + 1

    assert my_function(5) == 6
    assert my_function.__name__ == 'my_function'


def test_override_fallback_logic():
    """Test the fallback logic when typing.override is not available."""
    import importlib
    import typing

    override_module = sys.modules['collider.utils.compat.override']
    fake_typing = types.ModuleType('typing')
    fake_typing.__dict__.update(typing.__dict__)
    fake_typing.__dict__.pop('override', None)

    with patch.dict(sys.modules, {'typing': fake_typing}):
        importlib.reload(override_module)

        from collider.utils.compat.override import override

        @override
        def test_func():
            return 'fallback'

        assert test_func() == 'fallback'

    importlib.reload(override_module)


def test_override_real_logic():
    """Test that it uses the real typing.override if available (Python 3.12+)."""
    if sys.version_info >= (3, 12):
        from typing import override as real_override

        from collider.utils.compat.override import override

        assert override is real_override
