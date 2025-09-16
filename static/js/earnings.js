// yoppychat-nextjs razorpay setup/static/js/earnings.js

document.addEventListener('DOMContentLoaded', function() {
    const modal = document.getElementById('payoutModal');
    if (!modal) return;

    const openBtn = document.getElementById('requestPayoutBtn');
    const closeBtn = document.getElementById('closeModalBtn');
    
    const payoutRequestView = document.getElementById('payoutRequestView');
    const payoutDetailsView = document.getElementById('payoutDetailsView');
    const payoutDetailsForm = document.getElementById('payoutDetailsForm');
    const payoutForm = document.getElementById('payoutForm');
    const editPayoutDetailsBtn = document.getElementById('editPayoutDetailsBtn');
    const payoutDetailsTitle = document.getElementById('payoutDetailsTitle');

    // State variable to hold the creator's payout details in memory.
    let creatorPayoutDetails = null;
    
    // **THE FIX STARTS HERE**: Read details from the template on page load.
    try {
        const detailsAttr = modal.dataset.payoutDetails;
        if (detailsAttr && detailsAttr !== 'null' && detailsAttr.trim() !== '{}') {
            creatorPayoutDetails = JSON.parse(detailsAttr);
        }
    } catch (e) {
        console.error("Could not parse initial payout details:", e);
    }
    
    // --- Modal Control ---
    const openModal = () => {
        // Now, it checks the JavaScript variable instead of the HTML attribute.
        if (creatorPayoutDetails) {
            showRequestView(creatorPayoutDetails);
        } else {
            showDetailsView();
        }
        modal.style.display = 'flex';
    };
    const closeModal = () => modal.style.display = 'none';

    if (openBtn) openBtn.addEventListener('click', openModal);
    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    window.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
    });

    // --- View Switching Logic (No changes here) ---
    function showRequestView(details) {
        const maskedAccount = String(details.account_number).slice(-4).padStart(String(details.account_number).length, 'X');
        document.getElementById('payoutMethodName').textContent = `Bank Account: ${maskedAccount}`;
        payoutDetailsView.style.display = 'none';
        payoutRequestView.style.display = 'block';
    }

    function showDetailsView(details = null) {
        if (details) {
            payoutDetailsTitle.textContent = 'Edit Payout Details';
            document.getElementById('bank_account_name').value = details.name || '';
            document.getElementById('bank_account_number').value = details.account_number || '';
            document.getElementById('bank_ifsc_code').value = details.ifsc || '';
        } else {
            payoutDetailsTitle.textContent = 'Add Payout Details';
            payoutDetailsForm.reset();
        }
        payoutRequestView.style.display = 'none';
        payoutDetailsView.style.display = 'block';
    }

    // --- Event Listeners ---
    if (editPayoutDetailsBtn) {
        editPayoutDetailsBtn.addEventListener('click', () => {
            showDetailsView(creatorPayoutDetails);
        });
    }

    if (payoutDetailsForm) {
        payoutDetailsForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const formData = new FormData(this);
            const data = Object.fromEntries(formData.entries());

            fetch('/api/save_payout_details', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            })
            .then(res => res.json())
            .then(result => {
                if (result.status === 'success') {
                    showNotification(result.message, 'success');
                    // **THE FIX CONTINUES HERE**: Update the in-memory variable.
                    creatorPayoutDetails = data;
                    showRequestView(data);
                } else {
                    throw new Error(result.message);
                }
            })
            .catch(error => showNotification(error.message, 'error'));
        });
    }

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
                if (!res.ok) { return res.json().then(err => Promise.reject(err)); }
                return res.json();
            })
            .then(data => {
                showNotification(data.message, 'success');
                closeModal();
                setTimeout(() => window.location.reload(), 1500);
            })
            .catch(error => {
                showNotification(error.message || 'An error occurred.', 'error');
            });
        });
    }
});