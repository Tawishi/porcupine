_FUNNY = '''\
def foo(
    x,
     y
'''
_DEDENTED = '''\
def foo(
    x,
    y
'''
_BEFORE_Y = '3.5'
_AFTER_Y = '3.6'


# issue 65
def test_dedent_when_misaligned(filetab):
    filetab.settings.set('indent_size', 4)
    filetab.settings.set('tabs2spaces', True)
    filetab.update()

    filetab.textwidget.insert('end', _FUNNY)
    assert filetab.textwidget.dedent(_BEFORE_Y)
    assert filetab.textwidget.get('1.0', 'end - 1 char') == _DEDENTED


# issue 74
def test_doesnt_delete_stuff_far_away_from_cursor(filetab):
    filetab.settings.set('indent_size', 4)
    filetab.settings.set('tabs2spaces', True)
    filetab.update()

    filetab.textwidget.insert('end', _FUNNY)
    assert not filetab.textwidget.dedent(_AFTER_Y)
    assert filetab.textwidget.get('1.0', 'end - 1 char') == _FUNNY


def test_dedent_start_of_line(filetab):
    filetab.settings.set('indent_size', 4)

    for tabs2spaces in [True, False]:
        filetab.settings.set('tabs2spaces', tabs2spaces)
        filetab.update()

        filetab.textwidget.insert('end', (' '*4 if tabs2spaces else '\t') + 'a')
        assert filetab.textwidget.dedent('1.0')
        assert filetab.textwidget.get('1.0', 'end - 1 char') == 'a'
        assert not filetab.textwidget.dedent('1.0')
        assert filetab.textwidget.get('1.0', 'end - 1 char') == 'a'
        filetab.textwidget.delete('1.0', 'end')