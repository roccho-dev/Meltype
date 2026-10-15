scriptencoding utf-8
execute 'source ' . fnameescape(expand('<sfile>:p:h:h') . '/editor_context.vim')
let s:cases = 0
function! s:reset() abort
  enew!
  call setline(1, ['# 日本語のコメント', '元の本文'])
  call cursor(1, 3)
endfunction
function! s:refuse(code, operation) abort
  let before = getline(1, '$')
  try
    call a:operation()
    call assert_report('expected ' . a:code)
  catch
    call assert_match(a:code, v:exception)
  endtry
  call assert_equal(before, getline(1, '$'))
  let s:cases += 1
endfunction
call s:reset()
let g:p = {'id': 'B', 'representation': "採用候補\n二行目", 'meaning': {'kind': 'text'}, 'evidence': {'source': 'fixture'}}
let g:q = MeltypeObserve(1, 1)
call assert_equal(['# 日本語のコメント', '元の本文'], g:q.lines)
call assert_equal([1, 1], g:q.span)
call assert_equal([1, 3], g:q.cursor)
let receipt = MeltypeAppend(g:q, g:p)
call assert_equal(['# 日本語のコメント', '', '採用候補', '二行目', '元の本文'], getline(1, '$'))
call assert_equal(g:p, receipt.proposal)
call assert_true(receipt.afterRevision > receipt.beforeRevision)
let s:cases += 1
call s:refuse('MELTYPE_STALE_CONTEXT', {-> MeltypeAppend(g:q, g:p)})
call s:reset()
let g:q = MeltypeObserve(1, 1)
call cursor(2, 1)
call s:refuse('MELTYPE_STALE_CONTEXT', {-> MeltypeAppend(g:q, g:p)})
call s:reset()
let g:q = MeltypeObserve(1, 1)
call setline(2, '他の編集')
call s:refuse('MELTYPE_STALE_CONTEXT', {-> MeltypeAppend(g:q, g:p)})
call setline(2, '元の本文')
call s:refuse('MELTYPE_STALE_CONTEXT', {-> MeltypeAppend(g:q, g:p)})
call s:reset()
let g:q = MeltypeObserve(1, 1)
enew!
call setline(1, g:q.lines)
call cursor(1, 3)
call s:refuse('MELTYPE_STALE_CONTEXT', {-> MeltypeAppend(g:q, g:p)})
call s:reset()
let g:q = MeltypeObserve(1, 1)
setlocal readonly
call s:refuse('MELTYPE_UNSUPPORTED_BUFFER', {-> MeltypeAppend(g:q, g:p)})
setlocal noreadonly
call s:refuse('MELTYPE_INVALID_SPAN', {-> MeltypeObserve(0, 1)})
call s:refuse('MELTYPE_INVALID_SPAN', {-> MeltypeObserve(1, 99)})
call s:refuse('MELTYPE_INVALID_SELECTION', {-> MeltypeAppend(g:q, {'id': 'B'})})
let g:bad = deepcopy(g:p)
let g:bad.representation = "unsafe\e[2J"
call s:refuse('MELTYPE_UNSAFE_TEXT', {-> MeltypeAppend(g:q, g:bad)})
let g:bad.representation = repeat('x', 16385)
call s:refuse('MELTYPE_UNSAFE_TEXT', {-> MeltypeAppend(g:q, g:bad)})
call setline(2, repeat('x', 16385))
call s:refuse('MELTYPE_CONTEXT_TOO_LARGE', {-> MeltypeObserve(1, 1)})
if len(v:errors)
  call writefile(v:errors, '/dev/stderr')
  cquit 1
endif
call writefile([json_encode({'test': 'editor-context', 'cases': s:cases, 'status': 'PASS', 'editor': v:version})], '/dev/stdout')
qa!
