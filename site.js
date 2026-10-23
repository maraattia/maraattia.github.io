// Mobile menu toggle and back-to-top button, shared by every page.
const menuBtn = document.querySelector('.menu-btn');
const nav = document.getElementById('site-nav');

if (menuBtn && nav) {
    menuBtn.addEventListener('click', () => {
        const open = nav.classList.toggle('open');
        menuBtn.setAttribute('aria-expanded', String(open));
    });
    nav.querySelectorAll('a').forEach(a => a.addEventListener('click', () => {
        nav.classList.remove('open');
        menuBtn.setAttribute('aria-expanded', 'false');
    }));
}

const toTop = document.createElement('button');
toTop.className = 'to-top';
toTop.setAttribute('aria-label', 'Back to top');
toTop.innerHTML = '<i class="fas fa-arrow-up" aria-hidden="true"></i>';
document.body.appendChild(toTop);
toTop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
window.addEventListener('scroll', () => toTop.classList.toggle('visible', window.scrollY > 400), { passive: true });
