" this is sunil srivatsa's .vimrc file
scriptencoding utf-8

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
" VIM-PLUG (https://github.com/junegunn/vim-plug)
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
if !filereadable(expand('~/.vim/autoload/plug.vim'))
  echoerr 'vim-plug is missing; run ~/.dotfiles/setup.sh'
  finish
endif

call plug#begin('~/.vim/plugged')

Plug 'airblade/vim-gitgutter'
Plug 'altercation/vim-colors-solarized'
Plug 'jiangmiao/auto-pairs'
Plug 'junegunn/fzf', { 'dir': '~/.fzf', 'do': './install --all' }
Plug 'junegunn/fzf.vim'
Plug 'ntpeters/vim-better-whitespace'
Plug 'preservim/nerdcommenter'
Plug 'preservim/nerdtree', { 'on': 'NERDTreeToggle' }
Plug 'sheerun/vim-polyglot'
Plug 'thinca/vim-quickrun', { 'on': 'QuickRun' }
Plug 'tpope/vim-endwise'
Plug 'tpope/vim-fugitive'
Plug 'tpope/vim-repeat'
Plug 'tpope/vim-surround'
Plug 'dense-analysis/ale'
Plug 'xuyuanp/nerdtree-git-plugin', { 'on': 'NERDTreeToggle' }
Plug 'preservim/vim-indentline', { 'dir': '~/.vim/plugged/indentline' }

function! BuildYCM(info)
  " info is a dictionary with 3 fields
  " - name:   name of the plugin
  " - status: 'installed', 'updated', or 'unchanged'
  " - force:  set on PlugInstall! or PlugUpdate!
  if a:info.status == 'installed' || a:info.force
    !./install.py --js-completer
  endif
endfunction

Plug 'ycm-core/YouCompleteMe', { 'dir': '~/.vim/plugged/youcompleteme', 'do': function('BuildYCM') }

call plug#end()

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
" BASIC EDITING CONFIGURATION
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
" 2 space tabs
set tabstop=2
set shiftwidth=2
set softtabstop=2

" Keep editor state out of working directories.
let s:vim_state_home = exists('$XDG_STATE_HOME') && !empty($XDG_STATE_HOME)
  \ ? $XDG_STATE_HOME : expand('~/.local/state')
let s:vim_state_dir = s:vim_state_home . '/vim'
for s:directory in ['backup', 'swap', 'undo']
  let s:path = s:vim_state_dir . '/' . s:directory
  if !isdirectory(s:path) && !mkdir(s:path, 'p', 0700)
    throw 'unable to create Vim state directory: ' . s:path
  endif
endfor
let &backupdir = s:vim_state_dir . '/backup//'
let &directory = s:vim_state_dir . '/swap//'
let &undodir = s:vim_state_dir . '/undo//'
unlet s:directory s:path s:vim_state_dir s:vim_state_home

set backup            " retain backups outside the working tree
set undofile          " persist undo history across sessions
set autoindent        " copy the indent from the previous line
set autoread          " reload files when changed on disk, i.e. via git checkout
set cc=81             " highlight the 81st column
set expandtab         " insert spaces instead of tabs
set formatoptions+=j  " delete comment leaders when joining lines
set gdefault          " applies substitutions globally on lines
set hidden            " hides buffers instead of closing them
set hlsearch          " highlight all search matches
set ignorecase        " case-insensitive search
set incsearch         " search as you type
set laststatus=2      " always display the status line
set lazyredraw        " enable lazy redrawing
set nowrap            " disable wrapping
set number            " line numbers
set ruler             " displays the current cursor position
set scrolloff=3       " keep context above and below the cursor
set showcmd           " show the input of an incomplete command
set showmatch         " jump the cursor on matching braces/parens/brackets
set smartcase         " case-sensitive search if any caps
set splitbelow        " open horizontal splits below
set splitright        " open vertical splits to the right
set virtualedit=all   " allow the cursor to roam beyond defined text
set visualbell        " flash the screen instead of beeping

if has('unnamedplus')
  set clipboard=unnamedplus
elseif has('clipboard')
  set clipboard=unnamed
endif

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
" COLOR
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
set background=dark
colorscheme solarized

" change the 81st col to be gray
highlight ColorColumn ctermbg=gray

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
" KEY MAPPINGS
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
let mapleader = "\<Space>"
nnoremap <leader><leader> <C-^>
nnoremap <Leader>a :Rg <C-R><C-W><CR>
nnoremap <leader>b :Gblame<CR>
nnoremap <leader>e :lnext<CR>
nnoremap <leader>h :noh<CR>
nnoremap <leader>r :QuickRun<CR>
nnoremap <leader>t :NERDTreeToggle<CR>
nnoremap <leader>w <C-w>v<C-w>l:Files<CR>

" f5 to strip whitespace
nnoremap <silent> <F5> :StripWhitespace<CR>

" disable arrow keys
nnoremap <up> <nop>
nnoremap <down> <nop>
nnoremap <left> <nop>
nnoremap <right> <nop>
inoremap <up> <nop>
inoremap <down> <nop>
inoremap <left> <nop>
inoremap <right> <nop>

" get rid of the help key
inoremap <F1> <ESC>
nnoremap <F1> <ESC>
vnoremap <F1> <ESC>

" easier split navigations
nnoremap <C-J> <C-W><C-J>
nnoremap <C-K> <C-W><C-K>
nnoremap <C-L> <C-W><C-L>
nnoremap <C-H> <C-W><C-H>

" make the j and k work better with wrapped text
" if you hit j, it goes down a visual line, not a logical line
noremap j gj
noremap k gk

" toggle [i]nvisible characters
nmap <leader>i :set list!<CR>

" search with fzf
nmap <C-P> :Files<CR>
" files command with preview window
command! -bang -nargs=? -complete=dir Files
  \ call fzf#vim#files(<q-args>, fzf#vim#with_preview(), <bang>0)

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
" PLUGIN SETTINGS
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

" ale settings
let g:ale_open_list = 1
let g:ale_echo_msg_format = '[%linter%] %s [%severity%]'
let g:ale_lint_delay = 1000

" gitgutter update after 100ms delay
set updatetime=100

" gitgutter styling to use · instead of +/-
let g:gitgutter_sign_added = '∙'
let g:gitgutter_sign_modified = '∙'
let g:gitgutter_sign_removed = '∙'
let g:gitgutter_sign_modified_removed = '∙'

let g:indentLine_char = '¦'
let g:indentLine_fileTypeExclude = ['tex', 'markdown']


" disable folding in latex
let g:tex_conceal = ""

" disable concealing of double quotes
let g:vim_json_syntax_conceal = 0

" disable conealing and folding with markdown
let g:vim_markdown_conceal = 0
let g:vim_markdown_folding_disabled = 1

" close the ycm preview window after insertion or completion
let g:ycm_autoclose_preview_window_after_insertion = 1
let g:ycm_autoclose_preview_window_after_completion = 1

" use ripgrep
if executable('rg')
  " use rg over grep
  set grepprg=rg\ --vimgrep
endif

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
" CUSTOM AUTOCMDS
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
augroup vimrcEx
  " clear all autocmds in the group
  autocmd!

  " jump to last cursor position unless it's invalid, in an event handler, or a
  " git commit message
  autocmd BufReadPost *
    \ if &filetype !~ '^git\c' && line("'\"") > 0 && line("'\"") <= line("$") |
    \   exe "normal g`\"" |
    \ endif


  " .hql files are sql
  autocmd BufRead,BufNewFile *.hql set filetype=sql

  " automatically rebalance windows on vim resize
  autocmd VimResized * :wincmd =

  " close the quickfix / location list if it is the last window open
  autocmd WinEnter * if &buftype ==# 'quickfix' && winnr('$') == 1 | quit | endif
augroup END
