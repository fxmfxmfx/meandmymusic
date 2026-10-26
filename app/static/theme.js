'use strict';
window.applyMusicTheme = function(value){
 const colors={current:'#141615',light:'#f8f9fb',dark:'#131518',gruvbox:'#282828',mocha:'#1e1e2e'};
 const theme=Object.hasOwn(colors,value)?value:'current';
 document.documentElement.dataset.theme=theme;
 document.querySelector('meta[name="theme-color"]')?.setAttribute('content',colors[theme]);
 return theme;
};
try {
 applyMusicTheme(localStorage.getItem('mmm-theme'));
} catch { applyMusicTheme('current'); }
