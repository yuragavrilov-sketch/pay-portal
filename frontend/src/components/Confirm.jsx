import React from 'react';

export default function Confirm({ show, title, body, onConfirm, onCancel, variant = 'danger' }) {
  if (!show) return null;
  return (
    <div className="modal show d-block" style={{ background: 'rgba(0,0,0,.5)' }}>
      <div className="modal-dialog">
        <div className="modal-content">
          <div className="modal-header">
            <h5 className="modal-title">{title || 'Подтверждение'}</h5>
            <button className="btn-close" onClick={onCancel}></button>
          </div>
          <div className="modal-body">{body}</div>
          <div className="modal-footer">
            <button className="btn btn-secondary" onClick={onCancel}>Отмена</button>
            <button className={`btn btn-${variant}`} onClick={onConfirm}>Подтвердить</button>
          </div>
        </div>
      </div>
    </div>
  );
}
