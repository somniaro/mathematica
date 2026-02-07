# Configuración de latexmk para biblatex con biber
$pdflatex = 'lualatex %O %S';
$pdf_mode = 5;  # LuaLaTeX mode
$bibtex_use = 2;  # Use biber instead of bibtex
$biber = 'biber %O --bblencoding=utf8 -u -U --output_safechars %B';

