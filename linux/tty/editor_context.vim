" SPDX-License-Identifier: GPL-3.0-or-later
" Explicit host adapter: observe a comment; append a chosen representation.
" No mappings, IME interception, provider, disk write or Accepted mutation.
if exists('g:loaded_meltype_editor_context')
  finish
endif
let g:loaded_meltype_editor_context = 1
let s:session = printf('%d:%s', getpid(), reltimestr(reltime()))

function! MeltypeObserve(first, last) abort
  if &buftype !=# '' || &binary || !&modifiable || &readonly
    throw 'MELTYPE_UNSUPPORTED_BUFFER'
  endif
  if type(a:first) != v:t_number || type(a:last) != v:t_number
        \ || a:first < 1 || a:last < a:first || a:last > line('$')
    throw 'MELTYPE_INVALID_SPAN'
  endif
  let lines = getline(1, '$')
  if strlen(join(lines, "\n")) > 16384
    throw 'MELTYPE_CONTEXT_TOO_LARGE'
  endif
  return {'target': [s:session, win_getid(), bufnr('%'), expand('%:p')],
        \ 'revision': b:changedtick, 'cursor': getcurpos()[1:2],
        \ 'span': [a:first, a:last], 'lines': lines}
endfunction

function! MeltypeAppend(observed, proposal) abort
  if type(a:observed) != v:t_dict || type(get(a:observed, 'span', 0)) != v:t_list
        \ || len(a:observed.span) != 2
    throw 'MELTYPE_INVALID_OBSERVATION'
  endif
  " Re-observe inside the editor immediately before the only edit.
  if a:observed !=# MeltypeObserve(a:observed.span[0], a:observed.span[1])
    throw 'MELTYPE_STALE_CONTEXT'
  endif
  if type(a:proposal) != v:t_dict
        \ || type(get(a:proposal, 'id', 0)) != v:t_string || empty(a:proposal.id)
        \ || type(get(a:proposal, 'representation', 0)) != v:t_string
        \ || empty(a:proposal.representation) || !has_key(a:proposal, 'meaning')
        \ || !has_key(a:proposal, 'evidence')
    throw 'MELTYPE_INVALID_SELECTION'
  endif
  let text = a:proposal.representation
  let inserted = split(text, "\n", 1)
  if strlen(text) > 16384 || !empty(filter(copy(inserted),
        \ {_, line -> line =~# '[\x00-\x08\x0b-\x1f\x7f]'}))
    throw 'MELTYPE_UNSAFE_TEXT'
  endif
  let after = a:observed.span[1]
  if append(after, [''] + inserted) != 0
    throw 'MELTYPE_APPEND_FAILED'
  endif
  " No rollback or retry after an uncertain write; inspect the actual buffer.
  if getline(after + 1, after + 1 + len(inserted)) !=# [''] + inserted
    throw 'MELTYPE_APPEND_READBACK_UNKNOWN'
  endif
  return {'status': 'APPLIED', 'target': a:observed.target,
        \ 'beforeRevision': a:observed.revision, 'afterRevision': b:changedtick,
        \ 'span': [after + 1, after + 1 + len(inserted)],
        \ 'proposal': deepcopy(a:proposal)}
endfunction
