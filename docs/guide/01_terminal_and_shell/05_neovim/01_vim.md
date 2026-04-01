# Vim

This section is the Vim manual inside the Neovim chapter.

## Opening, closing, and help

Open a file:

```console
$ nvim pyproject.toml
```

Open multiple files:

```console
$ nvim README.md pyproject.toml AGENTS.md
```

Inside Neovim:

```text
:w        write
:q        quit
:wq       write and quit
:q!       quit without saving
:e file   edit file
:help x   open help
```

## Core terminology

- **file**: the thing on disk
- **buffer**: an open file in memory
- **window**: a viewport onto a buffer
- **split**: a window arrangement
- **tab**: a collection of windows

## Modes

```text
normal mode      movement and commands
insert mode      typing text
visual mode      selection
command mode     :commands
```

Essential mode commands:

```text
i      insert before cursor
a      insert after cursor
o      open line below
O      open line above
Esc    return to normal mode
```

## Movement cheat sheet

### Basic movement

```text
h j k l          left down up right
w b e            next word, previous word, end of word
0 ^ $            start of line, first non-whitespace, end of line
gg G             top of file, bottom of file
5j 10k           counts with movement
Ctrl-d Ctrl-u    half-page down, half-page up
zz zt zb         recenter, top, bottom
H M L            top, middle, bottom of visible screen
```

### Character movement

```text
f<char>          move to next char on line
t<char>          move until before next char on line
F<char>          move backward to char
T<char>          move backward until before char
;                repeat last char search
,                repeat last char search backward
```

### Structural movement

```text
%                matching pair
{ }              previous / next paragraph or block
( )              previous / next sentence
```

## The Vim grammar

Most useful commands are:

```text
operator + motion
```

Examples:

```text
dw      delete to next word
cw      change to next word
d$      delete to end of line
yip     yank inner paragraph
ci(     change inside parentheses
di{     delete inside braces
```

Useful operators:

```text
d       delete
c       change
y       yank
>       indent
<       outdent
gU      uppercase
gu      lowercase
```

## Insert and change commands

```text
i       insert before cursor
a       insert after cursor
I       insert at first non-whitespace on line
A       insert at end of line
o       open line below
O       open line above
s       substitute character
S       substitute line
C       change to end of line
cc      change line
```

## Delete, yank, paste, repeat

```text
x       delete character
dd      delete line
yy      yank line
p       paste after
P       paste before
u       undo
Ctrl-r  redo
.       repeat last change
```

## Visual mode

```text
v       visual mode
V       visual line mode
Ctrl-v  visual block mode
```

Examples:

```text
vaw     select a word
Vj      select current line and next line
>       indent selection
<       outdent selection
y       yank selection
d       delete selection
```

## Text objects

```text
ciw     change inner word
caw     change a word
ci"     change inside quotes
ci'     change inside single quotes
ci(     change inside parentheses
ci{     change inside braces
di[     delete inside brackets
vap     visually select a paragraph
yip     yank inner paragraph
```

## Search and substitute

```text
/pattern          search forward
?pattern          search backward
n / N             next / previous result
* / #             search word under cursor
:%s/old/new/g     replace throughout file
:%s/old/new/gc    replace with confirmation
:noh              clear search highlight
```

## Marks, alternate file, and jumplist

```text
ma      set mark a
'a      jump to line of mark a
`a      jump to exact position of mark a
Ctrl-^  alternate file
Ctrl-o  jump backward
Ctrl-i  jump forward
```

## Registers

```text
"a      named register a
"0      last yank
"_      black hole register
"+      system clipboard
```

Examples:

```text
"ayy     yank line into register a
"ap      paste register a
"_daw    delete a word without clobbering yank state
"+yy     yank to system clipboard
"+p      paste from system clipboard
```

## Macros

```text
qa       start recording into register a
q        stop recording
@a       replay macro a
@@       replay last macro
10@a     replay macro a ten times
```

## Quickfix and location lists

```text
:copen
:cclose
:cnext
:cprev
:cfirst
:clast

:lopen
:lclose
:lnext
:lprev
```

## Windows, splits, tabs, buffers

### Windows and splits

```text
Ctrl-w s        horizontal split
Ctrl-w v        vertical split
Ctrl-w w        next window
Ctrl-w h/j/k/l  move to window
Ctrl-w q        close window
Ctrl-w =        equalize windows
```

### Buffers

```text
:buffers
:bnext
:bprev
:b <name>
:bd
```

### Tabs

```text
:tabnew
:tabnext
:tabprev
:tabclose
```

## Settings and remaps

Useful settings:

```text
:set number
:set relativenumber
:set scrolloff=8
:set wrap
:set nowrap
```

Example remap in Lua:

```lua
vim.keymap.set("n", "<leader>w", ":w<CR>")
```
