(= (maxNewInputLoops) (empty))
(= (maxWakeLoops) (empty))
(= (sleepInterval) (empty))
(= (provider) (empty))
(= (maxOutputToken) (empty))
(= (reasoningMode) (empty))
(= (wakeupInterval) (empty))
(= (telegramAutonomousWake) (empty))
(= (memoryDirectory) (empty))

(= (initLoop)
   (progn (configure maxNewInputLoops 50) ;20
          (configure maxWakeLoops 1)
          (configure sleepInterval 1) ;10
          (configure provider Anthropic)
          (configure maxOutputToken 6000)
          (configure reasoningMode medium)
          (configure wakeupInterval 600) ;600=10 minutes
          (configure telegramAutonomousWake False)
          (configure memoryDirectory
               (joinPath ((py-call (helper.projectRootDirectory)) memory)))
          (change-state! &prevmsg __OMEGA_NO_CHANNEL_INPUT__)
          (change-state! &loops (maxNewInputLoops))
          (change-state! &telegramFollowup False)
          (change-state! &autonomousWake False)
          ; Initialize this even when an interactive transport suppresses idle inference.
          (change-state! &nextWakeAt (+ (get_time) (wakeupInterval)))
          (messagesClear)
          ))

(= (initKnowledge)
   (progn (log INFO "loop" "Initializing knowledge base")
          (log INFO "loop" (py-call (rag.init_knowledge (embeddingprovider))))
          ()))

(= (getContext)
   (string-safe (py-str ("PROMPT: " (getPrompt (provider)) " SKILLS: " (getSkillRules)
                         (newline) (getPromptExtensions) (newline)
                         " SAVE_PERMANENT_FILES_DIR: " (memoryDirectory)
                         " HISTORY: "
                         (if (and (== (commchannel) telegram)
                                  (not (or (get-state &telegramFollowup)
                                           (get-state &autonomousWake))))
                             "" (getHistory))
                         " TIME: " (get_time_as_string)))))

(= (addTelegramPromptExtension)
   (if (== (commchannel) telegram)
       (let $path (joinPath ((memoryDirectory) "tg_prompt.txt"))
            (if (exists-file $path)
                (add-prompt-extension tg-prompt (read-file $path))
                (progn (log WARN "loop" (TELEGRAM_PROMPT_NOT_FOUND: $path))
                       ())))
       ()))

(= (getPromptExtensions)
   (join (newline) (collapse (prompt-extension $_))))

; dynamic prompt extension placeholder to eliminate error when no extension is added
(= (prompt-extension placeholder) (empty))

(= (HandleError $result $error)
   (case $result (
      ((Error $cmd $msg) (let $new (append (get-state &error) (($msg $cmd)))
                           (let $errorstr (swrite $result)
                             (progn
                               (change-state! &error $new)
                               (let $error (strings-concat ("ALERT_FAILED" $errorstr))
                                 True)))))
      ($else False))))

(= (collectTools)
   (py-call (providers.getTools (getSkills))))

(= (messagesGet)
   (get-state &messages_all))

(= (messagesClear)
   (change-state! &messages_all (py-call (utils.listNew))))

(= (messagesAppend $item)
   (let $episodic (get-state &messages_all)
     (change-state! &messages_all (py-call (utils.listAppend $episodic $item)))))

(= (telegramInferenceReady $msgnew)
   (or $msgnew
       (or (get-state &telegramFollowup)
           (get-state &autonomousWake))))

(= (omega)
   (omega 1))
(= (omega $k)
   (progn (if (== $k 1) (progn (initConfig)
                               (initLoop)
                               (initLogger)
                               (applySecurityPolicy)
                               (initMemory)
                               (initKnowledge)
                               (initPlugins)
                               (initChannels)
                               (addTelegramPromptExtension)
                               (commChannelSend (version))
                               (llmProviderStart (provider)))
                        (change-state! &loops (- (get-state &loops) 1)))
          (let $prompt (getContext)
               (progn (log INFO "loop" (---------iteration $k))
                      (heartbeat $k)
                      (let* (($msgrcv (string-safe (repr (receive))))
                             ($msgnew (prog1 (py-call (channels.loopMessageIsNew $msgrcv (get-state &prevmsg)))
                                             (if $msgnew (change-state! &prevmsg $msgrcv) _)))
                             ($msg (get-state &prevmsg))
                             ($_ (if (and (> $k 1) $msgnew)
                                     (change-state! &loops (maxNewInputLoops)) _)))
                            ; Telegram is interactive by default. An opt-in autonomous wake
                            ; may start a turn, while tool followups continue until relayed.
                            (if (and (> (get-state &loops) 0)
                                     (or (!= (commchannel) telegram)
                                         (telegramInferenceReady $msgnew)))
                                (let $lastmessage
                                     (if $msgnew $msg "[NO NEW USER INPUT. CONTINUE AUTONOMOUS WORK. DO NOT REPEAT THE PREVIOUS RESPONSE. ONLY USE send FOR GENUINELY NEW INFORMATION OR WHEN USER INPUT IS NEEDED.]")
                                  (progn
                                    (change-state! &nextWakeAt (+ (get_time) (wakeupInterval)))
                                    (log INFO "loop" $lastmessage)
                                    (let $system (llmRequestMessage role "system" content $prompt)
                                      (progn
                                        (messagesAppend (llmRequestMessage role "user" content $lastmessage))
                                        (let $request (llmRequest $system (messagesGet) (maxOutputToken) (reasoningMode) (collectTools))
                                          (progn
                                            (log INFO "loop" (REQUEST: (py-str ($request))))
                                            (let $response (llmProviderChat $request)
                                              (progn
                                                (if (== (commchannel) telegram)
                                                    (change-state! &telegramFollowup
                                                        (llmResponseNeedsFollowup $response))
                                                    _)
                                                (log INFO "loop" (RESPONSE: (py-str ($response))))
                                                (let $respstr (py-call (providers.llmResponseToSExpr $response))
                                                  (let $resp (catch (sread $respstr))
                                                    (progn
                                                      (change-state! &error ())
                                                      (log INFO "loop" (RESPONSE: $resp))
                                                      (messagesClear)
                                                      (messagesAppend (llmToolCallMessage role "assistant"
                                                                                      calls (llmResponseCalls $response)
                                                                                      content "[TOOL CALL]"))
                                                      (let $sexpr (collapse
                                                            (let ($callid $s) (superpose $resp)
                                                              (let $result
                                                                (if (HandleError $s $error) $error
                                                                  (let $rc (catch (let $R (eval $s) (py-call (helper.normalize_string $R))))
                                                                    (if (HandleError $rc $error) $error (strings-concat ("SUCCESS, RETURN: " $rc)))))
                                                                (progn
                                                                  (log INFO "loop" (COMMAND_RETURN: ($s $result)))
                                                                  (messagesAppend (llmToolCallResponseMessage role "tool" callid $callid content (last_chars $result (maxFeedback))))
                                                                  (quote $s))))))
                                                        (progn
                                                          (if (or $msgnew (not (== $sexpr ())))
                                                              (addToHistory $msg $sexpr $msgnew) _)
                                                          (change-state! &autonomousWake False)
                                                          True))))))))))))
                                (if (> (get_time) (get-state &nextWakeAt))
                                    (let $_ (change-state! &loops (+ 1 (maxWakeLoops)))
                                         (if (and (== (commchannel) telegram)
                                                  (telegramAutonomousWake))
                                             (change-state! &autonomousWake True) _)) _)))
                      (sleep (sleepInterval))
                      (cut)
                      (gc)
                      (omega (+ 1 $k))))))
