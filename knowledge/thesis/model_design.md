Source: Haoyu Hu, FloodPred dissertation, PDF physical page 8 (section 3.3).

Shown in Fig. 2, two main modifications were added to place higher attention on flood conditions.
First, the standard regression loss was replaced with an customized asymmetric Huber loss
Second, a separate classification head estimates whether each forecast point represents a flood condition.
It is trained using binary cross-entropy with a positive-class weight of six because flood observations are less common than normal observations
The classification head is used only as a confidence gate.
The prediction results were calculated directly from the predicted water levels, not the classification score.
