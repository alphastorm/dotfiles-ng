" this is sunil srivatsa's .vimrc file

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
" VIM-PLUG (https://github.com/junegunn/vim-plug)
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
if empty(glob('~/.vim/autoload/plug.vim'))
  silent !curl -fLo ~/.vim/autoload/plug.vim --create-dirs
    \ https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
  autocmd VimEnter * PlugInstall --sync | source $MYVIMRC
endif

call plug#begin('~/.vim/plugged')

Plug '/usr/local/opt/fzf' | Plug 'junegunn/fzf.vim'
Plug 'airblade/vim-gitgutter'
Plug 'altercation/vim-colors-solarized'
Plug 'bling/vim-airline'
Plug 'edkolev/promptline.vim'
Plug 'fatih/vim-go', { 'tag': '*', 'do': ':GoInstallBinaries' }
Plug 'junegunn/fzf', { 'dir': '~/.fzf', 'do': './install --all' }
Plug 'mileszs/ack.vim'
Plug 'raimondi/delimitmate'
Plug 'scrooloose/nerdcommenter'
Plug 'scrooloose/nerdtree', { 'on': 'NERDTreeToggle' }
Plug 'sheerun/vim-polyglot'
Plug 'thinca/vim-quickrun', { 'on': 'QuickRun' }
Plug 'tpope/vim-dispatch'
Plug 'tpope/vim-endwise'
Plug 'tpope/vim-fugitive'
Plug 'tpope/vim-repeat'
Plug 'tpope/vim-surround'
Plug 'vim-airline/vim-airline-themes'
Plug 'w0rp/ale'
Plug 'xuyuanp/nerdtree-git-plugin', { 'on': 'NERDTreeToggle' }
Plug 'yggdroot/indentline'

function! BuildYCM(info)
  " info is a dictionary with 3 fields
  " - name:   name of the plugin
  " - status: 'installed', 'updated', or 'unchanged'
  " - force:  set on PlugInstall! or PlugUpdate!
  if a:info.status == 'installed' || a:info.force
    !./install.py --go-completer --js-completer
  endif
endfunction

Plug 'valloric/youcompleteme', { 'do': function('BuildYCM') }

call plug#end()

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
" BASIC EDITING CONFIGURATION
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
set nocompatible

" 2 space tabs
set tabstop=2
set shiftwidth=2
set softtabstop=2

" toggle paste for pasting unmodified text
" http://vim.wikia.com/wiki/Toggle_auto-indenting_for_code_paste
set pastetoggle=<F6>

" delete comment character when joining commented lines
" https://github.com/tpope/vim-sensible/blob/master/plugin/sensible.vim
if v:version > 703 || v:version == 703 && has("patch541")
  set formatoptions+=j
endif

set autoindent        " copy the indent from the previous line
set autoread          " reload files when changed on disk, i.e. via git checkout
set cc=81             " highlight the 81st column
set clipboard=unnamed " add support for the Mac OS X clipboard
set directory-=.      " don't store swapfiles in the current directory
set encoding=utf-8    " utf-8 encoding
set expandtab         " insert spaces instead of tabs
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
set scrolloff=3       " 2 lines at top/bottom of screen when scrolling
set showcmd           " show the input of an incomplete command
set showmatch         " jump the cursor on matching braces/parens/brackets
set smartcase         " case-sensitive search if any caps
set ttyfast           " improves redrawing
set virtualedit=all   " allow the cursor to roam beyond defined text
set visualbell        " flash the screen instead of beeping

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
" COLOR
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
set background=dark
colorscheme solarized

" highlight trailing whitespace
highlight ExtraWhitespace ctermbg=red guibg=red
match ExtraWhitespace /\s\+$/

" change the 81st col to be gray
highlight ColorColumn ctermbg=gray

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
" KEY MAPPINGS
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
let mapleader = "\<Space>"
nnoremap <leader><leader> <C-^>
nnoremap <leader>a :Ack<CR>
nnoremap <leader>b :Gblame<CR>
nnoremap <leader>e :lnext<CR>
nnoremap <leader>h :noh<CR>
nnoremap <leader>r :QuickRun<CR>
nnoremap <leader>t :NERDTreeToggle<CR>
nnoremap <leader>w <C-w>v<C-w>l:Files<CR>

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
" enable background search execution with vim-dispatch for ack.vim
let g:ack_use_dispatch = 1

let g:airline_powerline_fonts = 1
let g:airline_skip_empty_sections = 1

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

let g:promptline_theme = 'airline'
let g:promptline_preset = {
  \'a'    : [ promptline#slices#cwd({ 'dir_limit': 2 }) ],
  \'b'    : [ promptline#slices#vcs_branch(), '$(git rev-parse --short HEAD 2>/dev/null)', promptline#slices#git_status() ],
  \'c'    : [ promptline#slices#python_virtualenv() ],
  \'x'    : [ '$(date +"%H:%M:%S")' ],
  \'z'    : [ promptline#slices#host() ],
  \'warn' : [ promptline#slices#last_exit_code() ]}

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

  " for using rg with ack.vim
  let g:ackprg = 'rg --vimgrep'
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

  " disable colorcolumn for golang
  autocmd BufRead *.go setl cc=0

  " .hql files are sql
  autocmd BufRead,BufNewFile *.hql set filetype=sql

  " automatically rebalance windows on vim resize
  autocmd VimResized * :wincmd =

  " close the quickfix / location list if it is the last window open
  autocmd WinEnter * if &buftype ==# 'quickfix' && winnr('$') == 1 | quit | endif

  " prefer 2 space indent for python over pep8: disabled for uber
  " autocmd FileType python setl tabstop=2 shiftwidth=2 softtabstop=2
augroup END

""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
" STRIP TRAILING WHITESPACE
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""
function! <SID>StripTrailingWhitespaces()
  " preparation: save last search, and cursor position
  let _s=@/
  let l = line(".")
  let c = col(".")
  " do the business
  %s/\s\+$//e
  " clean up: restore previous search history, and cursor position
  let @/=_s
  call cursor(l, c)
endfunction
nnoremap <silent> <F5> :call <SID>StripTrailingWhitespaces()<CR>
