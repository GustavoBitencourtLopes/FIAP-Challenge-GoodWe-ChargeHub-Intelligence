// frontend/static/js/main.js
// Comportamentos compartilhados entre todas as páginas:
// - sombra na navbar ao rolar a página
// - menu hambúrguer no mobile
// - botão "voltar ao topo"

document.addEventListener('DOMContentLoaded', () => {
  const navbar = document.querySelector('.navbar');
  const navToggle = document.querySelector('.nav-toggle');
  const backToTop = document.querySelector('.back-to-top');

  // Sombra na navbar ao rolar
  const aoRolar = () => {
    if (!navbar) return;
    navbar.classList.toggle('scrolled', window.scrollY > 10);

    if (backToTop) {
      backToTop.classList.toggle('visible', window.scrollY > 400);
    }
  };

  window.addEventListener('scroll', aoRolar);
  aoRolar();

  // Menu hambúrguer (mobile)
  if (navToggle && navbar) {
    navToggle.addEventListener('click', () => {
      navbar.classList.toggle('menu-open');
    });
  }

  // Botão voltar ao topo
  if (backToTop) {
    backToTop.addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }
});