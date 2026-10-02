/**
 * Main
 */

'use strict';

const sidebarCollapseStorageKey = 'merge-pipeline:sidebar-collapsed';

let menu,
  animate;
document.addEventListener('DOMContentLoaded', function () {
  // class for ios specific styles
  if (navigator.userAgent.match(/iPhone|iPad|iPod/i)) {
    document.body.classList.add('ios');
  }
});

(function () {
  const sidebarCollapseToggle = document.getElementById('layout-menu-collapse');

  function updateSidebarCollapseToggle(collapsed) {
    if (!sidebarCollapseToggle) return;

    const label = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    sidebarCollapseToggle.setAttribute('aria-expanded', String(!collapsed));
    sidebarCollapseToggle.setAttribute('aria-label', label);
    sidebarCollapseToggle.setAttribute('title', label);
  }

  function readSidebarCollapsedPreference() {
    try {
      return window.localStorage.getItem(sidebarCollapseStorageKey) === 'true';
    } catch (_error) {
      return false;
    }
  }

  function storeSidebarCollapsedPreference(collapsed) {
    try {
      window.localStorage.setItem(sidebarCollapseStorageKey, String(collapsed));
    } catch (_error) {
      // Keep the control functional when browser storage is unavailable.
    }
  }

  // Initialize menu
  //-----------------

  let layoutMenuEl = document.querySelectorAll('#layout-menu');
  layoutMenuEl.forEach(function (element) {
    menu = new Menu(element, {
      orientation: 'vertical',
      closeChildren: false
    });
    // Change parameter to true if you want scroll animation
    window.Helpers.scrollToActive((animate = false));
    window.Helpers.mainMenu = menu;
  });

  // Initialize menu togglers and bind click on each
  let menuToggler = document.querySelectorAll('.layout-menu-toggle');
  menuToggler.forEach(item => {
    item.addEventListener('click', event => {
      event.preventDefault();
      if (window.Helpers.isSmallScreen()) {
        window.Helpers.toggleCollapsed();
        return;
      }

      const collapsed = !document.documentElement.classList.contains('layout-menu-collapsed');
      document.documentElement.classList.toggle('layout-menu-collapsed', collapsed);
      storeSidebarCollapsedPreference(collapsed);
      updateSidebarCollapseToggle(collapsed);
      window.dispatchEvent(new Event('resize'));
    });
  });

  // Display in main menu when menu scrolls
  let menuInnerContainer = document.getElementsByClassName('menu-inner'),
    menuInnerShadow = document.getElementsByClassName('menu-inner-shadow')[0];
  if (menuInnerContainer.length > 0 && menuInnerShadow) {
    menuInnerContainer[0].addEventListener('ps-scroll-y', function () {
      if (this.querySelector('.ps__thumb-y').offsetTop) {
        menuInnerShadow.style.display = 'block';
      } else {
        menuInnerShadow.style.display = 'none';
      }
    });
  }

  // Init helpers & misc
  // --------------------

  // Init BS Tooltip
  const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
  tooltipTriggerList.map(function (tooltipTriggerEl) {
    return new bootstrap.Tooltip(tooltipTriggerEl);
  });

  // Accordion active class
  const accordionActiveFunction = function (e) {
    if (e.type == 'show.bs.collapse' || e.type == 'show.bs.collapse') {
      e.target.closest('.accordion-item').classList.add('active');
    } else {
      e.target.closest('.accordion-item').classList.remove('active');
    }
  };

  const accordionTriggerList = [].slice.call(document.querySelectorAll('.accordion'));
  const accordionList = accordionTriggerList.map(function (accordionTriggerEl) {
    accordionTriggerEl.addEventListener('show.bs.collapse', accordionActiveFunction);
    accordionTriggerEl.addEventListener('hide.bs.collapse', accordionActiveFunction);
  });

  // Auto update layout based on screen size
  window.Helpers.setAutoUpdate(true);

  // Toggle Password Visibility
  window.Helpers.initPasswordToggle();

  // Speech To Text
  window.Helpers.initSpeechToText();

  // Manage menu expanded/collapsed with templateCustomizer & local storage
  //------------------------------------------------------------------

  // If current layout is horizontal OR current window screen is small (overlay menu) than return from here
  if (window.Helpers.isSmallScreen()) {
    return;
  }

  // If current layout is vertical and current window screen is > small

  // Restore the operator's desktop sidebar preference.
  const sidebarCollapsed = readSidebarCollapsedPreference();
  document.documentElement.classList.toggle('layout-menu-collapsed', sidebarCollapsed);
  updateSidebarCollapseToggle(sidebarCollapsed);
})();
// Utils
function isMacOS() {
  return /Mac|iPod|iPhone|iPad/.test(navigator.userAgent);
}
