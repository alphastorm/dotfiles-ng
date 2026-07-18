if exists('g:loaded_dotfiles_statusline')
  finish
endif
let g:loaded_dotfiles_statusline = 1

" Preserve the Solarized Airline palette without Airline's startup cost.
highlight DotStatusA cterm=bold ctermfg=15 ctermbg=14 gui=bold guifg=#fdf6e3 guibg=#93a1a1
highlight DotStatusAInsert cterm=bold ctermfg=15 ctermbg=3 gui=bold guifg=#fdf6e3 guibg=#b58900
highlight DotStatusAVisual cterm=bold ctermfg=15 ctermbg=5 gui=bold guifg=#fdf6e3 guibg=#d33682
highlight DotStatusAReplace ctermfg=15 ctermbg=1 guifg=#fdf6e3 guibg=#dc322f
highlight DotStatusB ctermfg=7 ctermbg=11 guifg=#eee8d5 guibg=#657b83
highlight DotStatusC ctermfg=10 ctermbg=0 guifg=#586e75 guibg=#073642
highlight DotStatusCModified ctermfg=14 ctermbg=0 guifg=#93a1a1 guibg=#073642
highlight DotStatusReadonly ctermfg=9 ctermbg=0 guifg=#cb4b16 guibg=#073642
highlight DotStatusInactive ctermfg=0 ctermbg=11 guifg=#073642 guibg=#657b83

highlight DotStatusAtoB ctermfg=14 ctermbg=11 guifg=#93a1a1 guibg=#657b83
highlight DotStatusAInsertToB ctermfg=3 ctermbg=11 guifg=#b58900 guibg=#657b83
highlight DotStatusAVisualToB ctermfg=5 ctermbg=11 guifg=#d33682 guibg=#657b83
highlight DotStatusAReplaceToB ctermfg=1 ctermbg=11 guifg=#dc322f guibg=#657b83
highlight DotStatusAtoC ctermfg=14 ctermbg=0 guifg=#93a1a1 guibg=#073642
highlight DotStatusAInsertToC ctermfg=3 ctermbg=0 guifg=#b58900 guibg=#073642
highlight DotStatusAVisualToC ctermfg=5 ctermbg=0 guifg=#d33682 guibg=#073642
highlight DotStatusAReplaceToC ctermfg=1 ctermbg=0 guifg=#dc322f guibg=#073642
highlight DotStatusBtoC ctermfg=11 ctermbg=0 guifg=#657b83 guibg=#073642
highlight DotStatusCtoC ctermfg=0 ctermbg=0 guifg=#073642 guibg=#073642
highlight DotStatusCtoB ctermfg=11 ctermbg=0 guifg=#657b83 guibg=#073642
highlight DotStatusBtoA ctermfg=14 ctermbg=11 guifg=#93a1a1 guibg=#657b83
highlight DotStatusBtoAInsert ctermfg=3 ctermbg=11 guifg=#b58900 guibg=#657b83
highlight DotStatusBtoAVisual ctermfg=5 ctermbg=11 guifg=#d33682 guibg=#657b83
highlight DotStatusBtoAReplace ctermfg=1 ctermbg=11 guifg=#dc322f guibg=#657b83
highlight DotStatusWarning ctermfg=15 ctermbg=9 guifg=#fdf6e3 guibg=#cb4b16
highlight DotStatusAtoWarning ctermfg=9 ctermbg=14 guifg=#cb4b16 guibg=#93a1a1
highlight DotStatusAInsertToWarning ctermfg=9 ctermbg=3 guifg=#cb4b16 guibg=#b58900
highlight DotStatusAVisualToWarning ctermfg=9 ctermbg=5 guifg=#cb4b16 guibg=#d33682
highlight DotStatusAReplaceToWarning ctermfg=9 ctermbg=1 guifg=#cb4b16 guibg=#dc322f

function! s:Escape(value) abort
  return substitute(a:value, '%', '%%', 'g')
endfunction

function! s:Mode() abort
  let value = mode(1)
  if value =~# '^i'
    return ['INSERT', 'DotStatusAInsert', 'DotStatusAInsertToB',
      \ 'DotStatusAInsertToC', 'DotStatusBtoAInsert',
      \ 'DotStatusAInsertToWarning']
  elseif value =~# '^R'
    return ['REPLACE', 'DotStatusAReplace', 'DotStatusAReplaceToB',
      \ 'DotStatusAReplaceToC', 'DotStatusBtoAReplace',
      \ 'DotStatusAReplaceToWarning']
  elseif value ==# 'v'
    return ['VISUAL', 'DotStatusAVisual', 'DotStatusAVisualToB',
      \ 'DotStatusAVisualToC', 'DotStatusBtoAVisual',
      \ 'DotStatusAVisualToWarning']
  elseif value ==# 'V'
    return ['V-LINE', 'DotStatusAVisual', 'DotStatusAVisualToB',
      \ 'DotStatusAVisualToC', 'DotStatusBtoAVisual',
      \ 'DotStatusAVisualToWarning']
  elseif value ==# "\<C-V>"
    return ['V-BLOCK', 'DotStatusAVisual', 'DotStatusAVisualToB',
      \ 'DotStatusAVisualToC', 'DotStatusBtoAVisual',
      \ 'DotStatusAVisualToWarning']
  elseif value =~# '^t'
    return ['TERMINAL', 'DotStatusAInsert', 'DotStatusAInsertToB',
      \ 'DotStatusAInsertToC', 'DotStatusBtoAInsert',
      \ 'DotStatusAInsertToWarning']
  endif
  return [value =~# '^c' ? 'COMMAND' : 'NORMAL', 'DotStatusA',
    \ 'DotStatusAtoB', 'DotStatusAtoC', 'DotStatusBtoA',
    \ 'DotStatusAtoWarning']
endfunction

function! s:ModeText(mode_name) abort
  let parts = [a:mode_name]
  if !empty(&key)
    call add(parts, '🔒')
  endif
  if &paste
    call add(parts, 'PASTE')
  endif
  if &spell
    call add(parts, 'SPELL')
  endif
  let path = expand('%:p')
  if !empty(path) && getfperm(path) =~# 'x'
    call add(parts, '⚙')
  endif
  return join(parts, '  ')
endfunction

function! s:Hunks() abort
  if !exists('*GitGutterGetHunkSummary') || !get(g:, 'gitgutter_enabled', 1)
    return ''
  endif
  let hunks = GitGutterGetHunkSummary()
  if hunks ==# [0, 0, 0]
    return ''
  endif
  let parts = []
  for index in range(0, 2)
    if hunks[index] > 0
      call add(parts, ['+', '~', '-'][index] . hunks[index])
    endif
  endfor
  return join(parts, ' ')
endfunction

function! s:Branch() abort
  return exists('*FugitiveHead') ? FugitiveHead() : ''
endfunction

function! s:AleProblemLine(type) abort
  if !exists('*ale#statusline#FirstProblem')
    return ''
  endif
  let problem = ale#statusline#FirstProblem(bufnr(''), a:type)
  if empty(problem)
    let problem = ale#statusline#FirstProblem(bufnr(''), 'style_' . a:type)
  endif
  return empty(problem) ? '' : '(L' . problem.lnum . ')'
endfunction

function! s:DiagnosticsStatus() abort
  if exists(':ALELint') == 2
    if ale#engine#IsCheckingBuffer(bufnr(''))
      return '...'
    endif
    let counts = ale#statusline#Count(bufnr(''))
    if type(counts) == type({})
      let errors = counts.error + counts.style_error
      let warnings = counts.total - errors
      let parts = []
      if errors
        call add(parts, 'E:' . errors . s:AleProblemLine('error'))
      endif
      if warnings
        call add(parts, 'W:' . warnings . s:AleProblemLine('warning'))
      endif
      if !empty(parts)
        return join(parts, ' ')
      endif
    endif
  endif

  let parts = []
  if exists('*youcompleteme#GetErrorCount')
    let errors = youcompleteme#GetErrorCount()
    if errors
      call add(parts, 'E:' . errors)
    endif
  endif
  if exists('*youcompleteme#GetWarningCount')
    let warnings = youcompleteme#GetWarningCount()
    if warnings
      call add(parts, 'W:' . warnings)
    endif
  endif
  return join(parts, ' ')
endfunction

function! DotfilesStatusline() abort
  let statusline_winid = get(g:, 'statusline_winid', win_getid())
  if statusline_winid != win_getid()
    return '%#DotStatusInactive# %<%f %m%r%=%y %p%% : %l/%L≡ ℅:%v '
  endif

  let mode_info = s:Mode()
  let hunks = s:Hunks()
  let branch = s:Branch()
  let filename = empty(expand('%:p'))
    \ ? '[No Name]' : fnamemodify(expand('%:p'), ':~')
  let encoding = empty(&fileencoding) ? &encoding : &fileencoding
  let diagnostics = s:DiagnosticsStatus()
  let section_b = empty(hunks) ? '' : ' ' . hunks
  if !empty(branch)
    let section_b .= '  ' . branch
  endif

  let line = '%#' . mode_info[1] . '# ' . s:ModeText(mode_info[0]) . ' '
  if empty(section_b)
    let line .= '%#' . mode_info[3] . '#'
  else
    let line .= '%#' . mode_info[2] . '#%#DotStatusB#'
    let line .= s:Escape(section_b) . ' %#DotStatusBtoC#'
  endif
  let line .= '%#' . (&modified ? 'DotStatusCModified' : 'DotStatusC') . '# '
  let line .= s:Escape(filename) . (&modified ? '+' : '')
  if &readonly
    let line .= ' %#DotStatusReadonly#'
  endif
  let line .= '%#DotStatusC# '
  let line .= '%='
  let line .= '%#DotStatusCtoC#%#DotStatusC# ' . s:Escape(&filetype) . ' '
  let line .= '%#DotStatusCtoB#%#DotStatusB# '
  let line .= s:Escape(encoding . '[' . &fileformat . ']') . ' '
  let line .= '%#' . mode_info[4] . '#%#' . mode_info[1]
  let line .= '# %p%% : %l/%L≡ ℅:%v '
  if !empty(diagnostics)
    let line .= '%#' . mode_info[5] . '#%#DotStatusWarning# '
    let line .= s:Escape(diagnostics) . ' '
  endif
  return line
endfunction

set statusline=%!DotfilesStatusline()

augroup dotfilesStatusline
  autocmd!
  autocmd User ALEJobStarted,ALELintPost redrawstatus
augroup END
