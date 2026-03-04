export default function RiskGauge({ score = 0, size = 120 }) {
  const radius = (size - 12) / 2;
  const circumference = 2 * Math.PI * radius;
  const center = size / 2;
  const normalizedScore = Math.min(100, Math.max(0, score));
  const progress = (normalizedScore / 100) * circumference;

  let color = '#10b981';
  let label = 'LOW';
  if (normalizedScore >= 70) {
    color = '#ef4444';
    label = 'HIGH';
  } else if (normalizedScore >= 30) {
    color = '#f59e0b';
    label = 'MEDIUM';
  }

  return (
    <div className="flex flex-col items-center gap-1" data-testid="risk-gauge">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {/* Background circle */}
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke="#1a1f2e"
          strokeWidth="6"
        />
        {/* Green zone arc (0-30%) */}
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke="#10b981"
          strokeWidth="6"
          strokeDasharray={`${circumference * 0.3} ${circumference * 0.7}`}
          strokeDashoffset={circumference * 0.25}
          opacity="0.15"
          transform={`rotate(-90 ${center} ${center})`}
        />
        {/* Amber zone arc (30-70%) */}
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke="#f59e0b"
          strokeWidth="6"
          strokeDasharray={`${circumference * 0.4} ${circumference * 0.6}`}
          strokeDashoffset={circumference * 0.25 - circumference * 0.3}
          opacity="0.15"
          transform={`rotate(-90 ${center} ${center})`}
        />
        {/* Red zone arc (70-100%) */}
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke="#ef4444"
          strokeWidth="6"
          strokeDasharray={`${circumference * 0.3} ${circumference * 0.7}`}
          strokeDashoffset={circumference * 0.25 - circumference * 0.7}
          opacity="0.15"
          transform={`rotate(-90 ${center} ${center})`}
        />
        {/* Progress arc */}
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={`${progress} ${circumference - progress}`}
          transform={`rotate(-90 ${center} ${center})`}
          style={{ transition: 'stroke-dasharray 0.6s ease' }}
        />
        {/* Score text */}
        <text
          x={center}
          y={center - 6}
          textAnchor="middle"
          dominantBaseline="middle"
          fill={color}
          fontSize="24"
          fontWeight="700"
          fontFamily="JetBrains Mono, monospace"
        >
          {normalizedScore}
        </text>
        <text
          x={center}
          y={center + 14}
          textAnchor="middle"
          dominantBaseline="middle"
          fill="#8892a4"
          fontSize="9"
          fontFamily="JetBrains Mono, monospace"
          letterSpacing="0.1em"
        >
          /100
        </text>
      </svg>
      <span
        className="text-[10px] font-mono uppercase tracking-wider font-bold"
        style={{ color }}
      >
        {label}
      </span>
    </div>
  );
}
