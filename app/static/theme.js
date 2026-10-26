'use strict';
try {
 const theme=localStorage.getItem('mmm-theme');
 document.documentElement.dataset.theme=['current','light','dark','gruvbox','mocha'].includes(theme)?theme:'current';
} catch { document.documentElement.dataset.theme='current'; }
