document.addEventListener('DOMContentLoaded', function() {
    const modal = document.getElementById('payoutModal');
    const openBtn = document.getElementById('requestPayoutBtn');
    const closeBtn = document.getElementById('closeModalBtn');
    const payoutForm = document.getElementById('payoutForm');

    if (openBtn) {
        openBtn.addEventListener('click', () => modal.style.display = 'flex');
    }
    if (closeBtn) {
        closeBtn.addEventListener('click', () => modal.style.display = 'none');
    }

    window.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.style.display = 'none';
        }
    });

    if (payoutForm) {
        payoutForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const amount = document.getElementById('payoutAmount').value;

            fetch('/api/request_payout', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ amount: amount })
            })
            .then(res => {
                if (!res.ok) {
                    return res.json().then(err => Promise.reject(err));
                }
                return res.json();
            })
            .then(data => {
                showNotification(data.message, 'success');
                modal.style.display = 'none';
                setTimeout(() => window.location.reload(), 1500);
            })
            .catch(error => {
                showNotification(error.message || 'An error occurred.', 'error');
            });
        });
    }
});