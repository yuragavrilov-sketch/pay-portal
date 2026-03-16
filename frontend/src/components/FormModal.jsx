import React from 'react';

export default function FormModal({ show, title, icon, size, onClose, onSubmit, error, children }) {
  if (!show) return null;
  return (
    <div className="modal show d-block" style={{ background: 'rgba(0,0,0,.5)' }} onClick={onClose}>
      <div className={`modal-dialog ${size ? 'modal-' + size : ''}`} onClick={e => e.stopPropagation()}>
        <form className="modal-content" onSubmit={e => { e.preventDefault(); onSubmit(); }}>
          <div className="modal-header">
            <h5 className="modal-title">
              {icon && <i className={`bi ${icon} me-2`}></i>}
              {title}
            </h5>
            <button type="button" className="btn-close" onClick={onClose}></button>
          </div>
          <div className="modal-body">
            {error && <div className="alert alert-danger py-2">{error}</div>}
            {children}
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Отмена</button>
            <button type="submit" className="btn btn-primary">
              <i className="bi bi-check-lg me-1"></i>Сохранить
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
