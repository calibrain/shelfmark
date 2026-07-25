import { NavLink } from 'react-router-dom';

interface LibraryNavigationProps {
  isOpen: boolean;
  onClose: () => void;
  onAddNew: () => void;
  onSettings: () => void;
}

const itemClassName = ({ isActive }: { isActive: boolean }) =>
  `flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${
    isActive ? 'bg-violet-500/15 text-violet-700 dark:text-violet-300' : 'hover-surface'
  }`;

export const LibraryNavigation = ({
  isOpen,
  onClose,
  onAddNew,
  onSettings,
}: LibraryNavigationProps) => (
  <>
    <button
      type="button"
      aria-label="Close navigation"
      className={`fixed inset-0 z-35 bg-black/40 transition-opacity lg:hidden ${
        isOpen ? 'opacity-100' : 'pointer-events-none opacity-0'
      }`}
      onClick={onClose}
      tabIndex={-1}
    />
    <aside
      className={`fixed top-0 bottom-0 left-0 z-40 flex w-64 flex-col border-r bg-(--bg) px-3 pt-20 pb-4 shadow-xl transition-transform lg:translate-x-0 lg:shadow-none ${
        isOpen ? 'translate-x-0' : '-translate-x-full'
      }`}
      style={{ borderColor: 'var(--border-muted)' }}
      aria-label="Primary navigation"
    >
      <nav className="space-y-1">
        <NavLink to="/library" className={itemClassName} onClick={onClose}>
          Library
        </NavLink>
        <button
          type="button"
          className="hover-surface w-full rounded-lg px-3 py-2.5 text-left text-sm font-medium"
          onClick={onAddNew}
        >
          Add New
        </button>
        <button
          type="button"
          className="hover-surface w-full rounded-lg px-3 py-2.5 text-left text-sm font-medium"
          onClick={onSettings}
        >
          Settings
        </button>
      </nav>
    </aside>
  </>
);
