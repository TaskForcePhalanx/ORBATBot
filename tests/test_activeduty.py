import pytest

from utils import activeduty


def test_clean_settings():
    ok = activeduty.clean_settings({'operations': '4', 'role_id': '1' * 18, 'enabled': 'on'})
    assert ok == {'enabled': True, 'role_id': '1' * 18, 'operations': 4}
    assert activeduty.clean_settings({'operations': '4'})['enabled'] is False


@pytest.mark.parametrize('form', [
    {'operations': 'x'}, {'operations': '0'}, {'operations': '21'},
    {'operations': '4', 'role_id': 'abc'},
    {'operations': '4', 'enabled': 'on'},
])
def test_clean_settings_refuses(form):
    with pytest.raises(ValueError):
        activeduty.clean_settings(form)


def test_active_users_any_name_any_recording():
    mapped = {'Alice': '1', 'Alice2': '1', 'Bob': '2'}
    assert activeduty.active_users([['Alice', 'Nobody'], ['Bob'], []], mapped) == {'1', '2'}
    assert activeduty.active_users([['Nobody']], mapped) == set()
    assert activeduty.active_users([], mapped) == set()


def test_changes():
    give, take = activeduty.changes({'1', '2', '3'}, {'2', '4'}, {'1', '2', '4'})
    assert give == ['1']        # 3 left the server
    assert take == ['4']        # 4 holds it without having played
