// NewClaim page: FNOL wizard → live pipeline run → persisted decision.
//
// Submission hands off to the durable worker; the wizard state dies with the
// draft, the pipeline board lives in the query cache (patched by the SSE event
// hook through the pure reducer), and the decision panel reads only the
// persisted claim record — reload or replay lands on the same view.
import { useCallback, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { FilePlus2, RotateCcw } from 'lucide-react';
import { queryKeys, useClaim, useSubmitClaim } from '@/lib/queries';
import { usePipelineEvents } from '@/hooks/usePipelineEvents';
import { INITIAL_PIPELINE_STATE, reducePipelineEvent } from './pipelineState';
import FNOLWizard from './wizard/FNOLWizard';
import PipelineBoard from './PipelineBoard';
import DecisionPanel from './DecisionPanel';
import { uploadClaimFiles } from './uploads';

// Claim record statuses the pipeline can settle on.
const TERMINAL_STATUSES = [
  'approved', 'auto_approved', 'rejected', 'under_review', 'escalate', 'escalated', 'failed',
];

export default function NewClaimPage() {
  const [claimId, setClaimId] = useState(null);
  const [uploadNotice, setUploadNotice] = useState('');
  const queryClient = useQueryClient();
  const submitClaim = useSubmitClaim();

  // Board state lives in the cache so the SSE hook, this page, and tests all
  // see one source of truth; staleTime=Infinity because only events write it.
  const pipelineQuery = useQuery({
    queryKey: queryKeys.pipeline(claimId ?? 'none'),
    queryFn: () => INITIAL_PIPELINE_STATE,
    enabled: Boolean(claimId),
    staleTime: Infinity,
  });

  const onEvent = useCallback((event) => {
    if (!claimId) return;
    const key = queryKeys.pipeline(claimId);
    const prev = queryClient.getQueryData(key) || INITIAL_PIPELINE_STATE;
    const next = reducePipelineEvent(prev, event);
    queryClient.setQueryData(key, next);
    // Terminal: the record now carries the persisted truth — refetch it.
    if (next.status !== 'running') {
      queryClient.invalidateQueries({ queryKey: ['claims', claimId] });
      queryClient.invalidateQueries({ queryKey: ['claims'] });
    }
  }, [claimId, queryClient]);

  const { connectionState } = usePipelineEvents({
    claimId,
    enabled: Boolean(claimId),
    onEvent,
    invalidateOnReconnect: ['claims'],
  });

  const claimQuery = useClaim(claimId);
  const record = claimQuery.data;

  const pipeline = pipelineQuery.data || INITIAL_PIPELINE_STATE;
  const decisionReady =
    record && TERMINAL_STATUSES.includes(record.status) && pipeline.status !== 'running';

  const handleSubmit = useCallback(
    async (draft, files) => {
      const res = await submitClaim.mutateAsync({
        policyNumber: draft.policyNumber,
        holderName: draft.holderName || '',
        incidentDate: draft.incidentDate,
        incidentType: draft.incidentType,
        claimedAmount: Number(draft.estimatedCost) || 0,
        description: draft.description,
        contactEmail: draft.contactEmail || '',
        documentText: draft.documentText || '',
      });
      const newId = res.claimId;
      queryClient.setQueryData(queryKeys.pipeline(newId), INITIAL_PIPELINE_STATE);
      setClaimId(newId);
      // Attachments ride along after the claim exists (best effort: a failed
      // upload is surfaced, never fatal to the submission).
      if (files.length > 0) {
        const results = await uploadClaimFiles(newId, files);
        const failed = results.filter((r) => r.status === 'rejected').length;
        setUploadNotice(failed > 0 ? `${failed} attachment(s) failed to upload.` : '');
      }
      return res;
    },
    [submitClaim, queryClient],
  );

  const reset = useCallback(() => {
    if (claimId) queryClient.removeQueries({ queryKey: queryKeys.pipeline(claimId) });
    setClaimId(null);
    setUploadNotice('');
    claimQuery.refetch().catch(() => {});
  }, [claimId, queryClient, claimQuery]);

  return (
    <div className="page-enter" data-testid="new-claim-page">
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-3">
          <FilePlus2 className="w-5 h-5 text-[#3b82f6]" />
          <h1
            className="text-2xl font-bold tracking-tight uppercase"
            style={{ fontFamily: 'Space Grotesk' }}
          >
            File a Claim
          </h1>
        </div>
        {claimId && (
          <button
            data-testid="new-claim-after-complete"
            onClick={reset}
            className="flex items-center gap-2 bg-[#3b82f6] hover:bg-[#3b82f6]/90 text-white rounded-none font-medium text-sm px-4 py-2 uppercase tracking-wide transition-colors duration-200"
          >
            <RotateCcw className="w-4 h-4" /> New Claim
          </button>
        )}
      </div>

      {uploadNotice && (
        <div
          className="mb-4 border border-[#f59e0b]/40 bg-[#f59e0b]/5 text-[#f59e0b] text-xs font-mono px-3 py-2 rounded-sm"
          data-testid="upload-notice"
        >
          {uploadNotice}
        </div>
      )}

      {!claimId && <FNOLWizard onSubmit={handleSubmit} />}

      {claimId && (
        <>
          <PipelineBoard claimId={claimId} pipeline={pipeline} connectionState={connectionState} />
          {decisionReady && <DecisionPanel claim={record} />}
        </>
      )}
    </div>
  );
}
